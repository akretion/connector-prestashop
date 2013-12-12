# -*- encoding: utf-8 -*-

from decimal import Decimal

from openerp import netsvc
from openerp.osv import fields
from openerp.osv import orm

from openerp.addons.connector.unit.mapper import mapping
from openerp.addons.connector.unit.mapper import only_create

from .backend import prestashop
from .unit.backend_adapter import GenericAdapter
from .unit.mapper import PrestashopImportMapper
from .unit.import_synchronizer import PrestashopImportSynchronizer


class account_invoice(orm.Model):
    _inherit = 'account.invoice'

    _columns = {
        'prestashop_bind_ids': fields.one2many(
            'prestashop.account.invoice',
            'openerp_id',
            string="Prestashop Bindings"
        ),
    }

    def action_move_create(self, cr, uid, ids, context=None):
        so_obj = self.pool.get('prestashop.sale.order')
        line_replacement = {}
        for invoice in self.browse(cr, uid, ids, context=context):
            so_ids = so_obj.search(cr, uid, [('name', '=', invoice.origin)])
            if not so_ids:
                continue
            sale_order = so_obj.browse(cr, uid, so_ids[0])
            discount_product_id = sale_order.backend_id.discount_product_id.id

            for invoice_line in invoice.invoice_line:
                if invoice_line.product_id.id != discount_product_id:
                    continue
                amount = invoice_line.price_subtotal
                if invoice.partner_id.parent_id:
                    partner_id = invoice.partner_id.parent_id.id
                else:
                    invoice.partner_id.id
                refund_id = self._find_refund(
                    cr, uid, -1 * amount, partner_id,
                    context=context)
                if refund_id:
                    self.pool.get('account.invoice.line').unlink(
                        cr, uid, invoice_line.id)
                    line_replacement[invoice.id] = refund_id
                    self.button_reset_taxes(cr, uid, [invoice.id],
                                            context=context)

        result = super(account_invoice, self).action_move_create(cr, uid, ids, context=None)
        ## reconcile invoice with refund
        move_line_obj = self.pool.get('account.move.line')
        for invoice_id, refund_id in line_replacement.items():
            self._reconcile_invoice_refund(cr, uid, invoice_id, refund_id, context=context)
        return result

    def _reconcile_invoice_refund(self, cr, uid, invoice_id, refund_id, context=None):
        move_line_obj = self.pool.get('account.move.line')
        invoice_obj = self.pool.get('account.invoice')
        
        invoice = invoice_obj.browse(cr, uid, invoice_id, context=context)
        refund = invoice_obj.browse(cr, uid, refund_id, context=context)

        move_line_ids = move_line_obj.search(cr, uid, [
            ('move_id', '=', invoice.move_id.id),
            ('debit', '!=', 0.0),
        ], context=context)
        move_line_ids += move_line_obj.search(cr, uid, [
            ('move_id', '=', refund.move_id.id),
            ('credit', '!=', 0.0),
        ], context=context)
        move_line_obj.reconcile_partial(cr, uid, move_line_ids, context=context)

    def _find_refund(self, cr, uid, amount, partner_id, context=None):
        ids = self.search(cr, uid, [
            ('amount_total', '=', amount),
            ('type', '=', 'out_refund'),
            ('state', '=', 'open'),
            ('partner_id', '=', partner_id),
        ])
        if not ids:
            return None
        return ids[0]


class prestashop_refund(orm.Model):
    _name = 'prestashop.refund'
    _inherit = 'prestashop.binding'
    _inherits = {'account.invoice': 'openerp_id'}

    _columns = {
        'openerp_id': fields.many2one(
            'account.invoice',
            string='Invoice',
            required=True,
            ondelete='cascade',
        ),
        'prestashop_refund_ids': fields.one2many(
            'prestashop.refund.line',
            'prestashop_refund_id',
            'Prestashop refund lines'
        ),
    }


@prestashop
class RefundAdapter(GenericAdapter):
    _model_name = 'prestashop.refund'
    _prestashop_model = 'order_slips'


@prestashop
class RefundImport(PrestashopImportSynchronizer):
    _model_name = 'prestashop.refund'

    def _import_dependencies(self):
        record = self.prestashop_record
        self._check_dependency(record['id_customer'], 'prestashop.res.partner')
        self._check_dependency(record['id_order'], 'prestashop.sale.order')

    def _after_import(self, refund_id):
        context = self.session.context
        context['company_id'] = self.backend_record.company_id.id
        refund = self.session.browse('prestashop.refund', refund_id)
        erp_id = refund.openerp_id.id
        invoice_obj = self.session.pool.get('account.invoice')
        invoice_obj.button_reset_taxes(
            self.session.cr,
            self.session.uid,
            [erp_id],
            context=context
        )

        invoice = self.session.browse('account.invoice', erp_id)
        #assert invoice.amount_total == float(self.prestashop_record['amount']), (
        #    'amounts in openerp (%f) and prestashop (%s) are not the same' % (
        #        invoice.amount_total, self.prestashop_record['amount']))

        if invoice.amount_total == float(self.prestashop_record['amount']):
            wf_service = netsvc.LocalService("workflow")
            wf_service.trg_validate(self.session.uid, 'account.invoice',
                                    erp_id, 'invoice_open', self.session.cr)


@prestashop
class RefundMapper(PrestashopImportMapper):
    _model_name = 'prestashop.refund'

    direct = [
        ('id', 'name'),
        ('date_add', 'date_invoice'),
    ]

    @mapping
    def journal_id(self, record):
        journal_ids = self.session.search('account.journal', [
            ('company_id', '=', self.backend_record.company_id.id),
            ('type', '=', 'sale_refund'),
        ])
        return {'journal_id': journal_ids[0]}

    @mapping
    def origin(self, record):
        binder = self.get_binder_for_model('prestashop.sale.order')
        sale_order_id = binder.to_openerp(record['id_order'], unwrap=True)
        sale_order = self.session.read('prestashop.sale.order', sale_order_id, ['name'])
        return {'origin': sale_order['name']}

    @mapping
    def comment(self, record):
        return {'comment': 'Montant dans prestashop : %s' % (record['amount'])}

    @mapping
    @only_create
    def invoice_lines(self, record):
        slip_details = record.get('associations', {}).get('order_slip_details', []).get('order_slip_detail', [])
        if isinstance(slip_details, dict):
            slip_details = [slip_details]
        lines = []
        shipping_line = self._invoice_line_shipping(record)
        if shipping_line is not None:
            lines.append((0, 0, shipping_line))
        for slip_detail in slip_details:
            line = self._invoice_line(slip_detail)
            lines.append((0, 0, line))
        return {'invoice_line': lines}

    def _invoice_line_shipping(self, record):
        order_line = self._get_shipping_order_line(record)
        if order_line is None:
            return None
        return {
            'quantity': 1,
            'product_id': order_line['product_id'][0],
            'name': order_line['name'],
            'invoice_line_tax_id': [(6, 0, order_line['tax_id'])],
            'price_unit': record['shipping_cost_amount'],
            'discount': order_line['discount'],
        }

    def _get_shipping_order_line(self, record):
        binder = self.get_binder_for_model('prestashop.sale.order')
        sale_order_id = binder.to_openerp(record['id_order'], unwrap=True)
        sale_order = self.session.browse('prestashop.sale.order', sale_order_id)

        if not sale_order.carrier_id:
            return None

        sale_order_line_ids = self.session.search('sale.order.line', [
            ('order_id', '=', sale_order_id),
            ('product_id', '=', sale_order.carrier_id.product_id.id),
        ])
        if not sale_order_line_ids:
            return None
        return self.session.read('sale.order.line', sale_order_line_ids[0], [])

    def _invoice_line(self, record):
        order_line = self._get_order_line(record['id_order_detail'])
        tax_ids = []
        for tax in order_line.tax_id:
            tax_ids.append(tax.id)
        return {
            'quantity': record['product_quantity'],
            'product_id': order_line.product_id.id,
            'name': order_line.name,
            'invoice_line_tax_id': [(6, 0, tax_ids)],
            'price_unit': order_line.price_unit,
            'discount': order_line.discount,
        }

    def _get_order_line(self, order_details_id):
        order_line_id = self.session.search('prestashop.sale.order.line', [
            ('prestashop_id', '=', order_details_id),
            ('backend_id', '=', self.backend_record.id),
        ])
        context = self.session.context
        context['company_id'] = self.backend_record.company_id.id
        return self.session.pool.get('prestashop.sale.order.line').browse(
            self.session.cr,
            self.session.uid,
            order_line_id[0],
            context=context
        )

    @mapping
    def type(self, record):
        return {'type': 'out_refund'}

    @mapping
    def partner_id(self, record):
        binder = self.get_binder_for_model('prestashop.res.partner')
        partner_id = binder.to_openerp(record['id_customer'], unwrap=True)
        return {'partner_id': partner_id}

    @mapping
    def account_id(self, record):
        context = self.session.context
        context['company_id'] = self.backend_record.company_id.id
        binder = self.get_binder_for_model('prestashop.res.partner')
        partner_id = binder.to_openerp(record['id_customer'])
        partner = self.session.pool['prestashop.res.partner'].browse(
            self.session.cr,
            self.session.uid,
            partner_id,
            context=context
        )
        return {'account_id': partner.property_account_receivable.id}

    @mapping
    def company_id(self, record):
        return {'company_id': self.backend_record.company_id.id}

    @mapping
    def backend_id(self, record):
        return {'backend_id': self.backend_record.id}

