# -*- coding: utf-8 -*-
#############################################################################
#
#    Prestashoperpconnect : OpenERP-PrestaShop connector
#    Copyright (C) 2013 Akretion (http://www.akretion.com/)
#    Copyright (C) 2013 Camptocamp SA
#    @author: Alexis de Lattre <alexis.delattre@akretion.com>
#    @author Sébastien BEAU <sebastien.beau@akretion.com>
#    @author: Guewen Baconnier
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program. If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################


import logging
from datetime import datetime

from openerp.osv import fields, orm


from openerp.tools import DEFAULT_SERVER_DATETIME_FORMAT
from openerp.addons.connector.session import ConnectorSession
from .unit.import_synchronizer import (
    import_batch,
    import_customers_since,
    import_orders_since,
    import_products,
    import_carriers)
from .unit.direct_binder import DirectBinder
from .connector import get_environment

_logger = logging.getLogger(__name__)


class prestashop_backend(orm.Model):
    _name = 'prestashop.backend'
    _doc = 'Prestashop Backend'
    _inherit = 'connector.backend'

    _backend_type = 'prestashop'

    def _select_versions(self, cr, uid, context=None):
        """ Available versions

        Can be inherited to add custom versions.
        """
        return [('1.5', '1.5')]

    _columns = {
        'version': fields.selection(
            _select_versions,
            string='Version',
            required=True),
        'location': fields.char('Location'),
        'webservice_key': fields.char(
            'Webservice key',
            help="You have to put it in 'username' of the PrestaShop "
                 "Webservice api path invite"
        ),
        'warehouse_id': fields.many2one(
            'stock.warehouse',
            'Warehouse',
            required=True,
            help='Warehouse used to compute the stock quantities.'
        ),
        'taxes_included': fields.boolean("Use tax included prices"),
        'import_partners_since': fields.datetime('Import partners since'),
        'import_orders_since': fields.datetime('Import Orders since'),
        'language_ids': fields.one2many(
            'prestashop.res.lang',
            'backend_id',
            'Languages'
        ),
        'company_id': fields.many2one('res.company', 'Company', select=1, required=True),
    }

    _defaults = {
        'company_id': lambda s,cr,uid,c: s.pool.get('res.company')._company_default_get(cr, uid, 'prestashop.backend', context=c),
    }

    def synchronize_metadata(self, cr, uid, ids, context=None):
        if not hasattr(ids, '__iter__'):
            ids = [ids]
        session = ConnectorSession(cr, uid, context=context)
        for backend_id in ids:
            for model in ('prestashop.shop.group',
                          'prestashop.shop'):
                # import directly, do not delay because this
                # is a fast operation, a direct return is fine
                # and it is simpler to import them sequentially
                import_batch(session, model, backend_id)
        return True

    def synchronize_basedata(self, cr, uid, ids, context=None):
        if not hasattr(ids, '__iter__'):
            ids = [ids]
        session = ConnectorSession(cr, uid, context=context)
        for backend_id in ids:
            for model_name in [
                'prestashop.res.lang',
                'prestashop.res.country',
                'prestashop.res.currency',
                'prestashop.account.tax',
            ]:
                env = get_environment(session, model_name, backend_id)
                directBinder = env.get_connector_unit(DirectBinder)
                directBinder.run()

            import_batch(session, 'prestashop.account.tax.group', backend_id)
            import_batch(session, 'prestashop.sale.order.state', backend_id)
        return True

    def import_customers_since(self, cr, uid, ids, context=None):
        if not hasattr(ids, '__iter__'):
            ids = [ids]
        session = ConnectorSession(cr, uid, context=context)
        for backend_record in self.browse(cr, uid, ids, context=context):
            since_date = None
            if backend_record.import_partners_since:
                since_date = datetime.strptime(
                    backend_record.import_partners_since,
                    DEFAULT_SERVER_DATETIME_FORMAT
                )
            import_customers_since.delay(
                session,
                backend_record.id,
                since_date,
                priority=10,
            )

        return True

    def import_products(self, cr, uid, ids, context=None):
        if not hasattr(ids, '__iter__'):
            ids = [ids]
        session = ConnectorSession(cr, uid, context=context)
        for backend_id in ids:
            import_products.delay(session, backend_id, priority=10)
        return True

    def import_carriers(self, cr, uid, ids, context=None):
        if not hasattr(ids, '__iter__'):
            ids = [ids]
        session = ConnectorSession(cr, uid, context=context)
        for backend_id in ids:
            import_carriers.delay(session, backend_id, priority=10)
        return True

    def update_product_stock_qty(self, cr, uid, ids, context=None):
        if not hasattr(ids, '__iter__'):
            ids = [ids]
        ps_product_obj = self.pool['prestashop.product.product']
        product_ids = ps_product_obj.search(
            cr,
            uid,
            [('backend_id', 'in', ids)],
            context=context
        )
        ps_product_obj.recompute_prestashop_qty(cr, uid, product_ids,
                                                context=context)
        return True

    def import_sale_orders(self, cr, uid, ids, context=None):
        if not hasattr(ids, '__iter__'):
            ids = [ids]
        session = ConnectorSession(cr, uid, context=context)
        for backend_record in self.browse(cr, uid, ids, context=context):
            since_date = None
            if backend_record.import_orders_since:
                since_date = datetime.strptime(
                    backend_record.import_orders_since,
                    DEFAULT_SERVER_DATETIME_FORMAT
                )
            import_orders_since.delay(
                session,
                backend_record.id,
                since_date,
                priority=5,
            )
        return True

    def _scheduler_launch(self, cr, uid, callback, domain=None,
                          context=None):
        if domain is None:
            domain = []
        ids = self.search(cr, uid, domain, context=context)
        if ids:
            callback(cr, uid, ids, context=context)

    def _scheduler_update_product_stock_qty(self, cr, uid, domain=None,
                                            context=None):
        self._scheduler_launch(cr, uid, self.update_product_stock_qty,
                               domain=domain, context=context)

    def _scheduler_import_sale_orders(self, cr, uid, domain=None, context=None):
        self._scheduler_launch(cr, uid, self.import_sale_orders, domain=domain,
                               context=context)

    def _scheduler_import_customers(self, cr, uid, domain=None, context=None):
        self._scheduler_launch(cr, uid, self.import_custmers_since,
                               domain=domain, context=context)

    def _scheduler_import_products(self, cr, uid, domain=None, context=None):
        self._scheduler_launch(cr, uid, self.import_products, domain=domain,
                               context=context)

    def _scheduler_import_carriers(self, cr, uid, domain=None, context=None):
        self._scheduler_launch(cr, uid, self.import_carriers, domain=domain,
                               context=context)

class prestashop_binding(orm.AbstractModel):
    _name = 'prestashop.binding'
    _inherit = 'external.binding'
    _description = 'PrestaShop Binding (abstract)'

    _columns = {
        # 'openerp_id': openerp-side id must be declared in concrete model
        'backend_id': fields.many2one(
            'prestashop.backend',
            'PrestaShop Backend',
            required=True,
            ondelete='restrict'),
        # TODO : do I keep the char like in Magento, or do I put a PrestaShop ?
        'prestashop_id': fields.integer('ID on PrestaShop'),
    }

    # the _sql_contraints cannot be there due to this bug:
    # https://bugs.launchpad.net/openobject-server/+bug/1151703


# TODO remove external.shop.group from connector_ecommerce
class prestashop_shop_group(orm.Model):
    _name = 'prestashop.shop.group'
    _inherit = 'prestashop.binding'
    _description = 'PrestaShop Shop Group'

    _columns = {
        'name': fields.char('Name', required=True),
        'shop_ids': fields.one2many(
            'prestashop.shop',
            'shop_group_id',
            string="Shops",
            readonly=True),
        'company_id': fields.related('backend_id', 'company_id', type="many2one", relation="res.company",string='Company', store=False),
    }

    _sql_constraints = [
        ('prestashop_uniq', 'unique(backend_id, prestashop_id)',
         'A shop group with the same ID on PrestaShop already exists.'),
    ]


# TODO migrate from sale.shop
class prestashop_shop(orm.Model):
    _name = 'prestashop.shop'
    _inherit = 'prestashop.binding'
    _description = 'PrestaShop Shop'

    _inherits = {'sale.shop': 'openerp_id'}

    def _get_shop_from_shopgroup(self, cr, uid, ids, context=None):
        return self.pool.get('prestashop.shop').search(
            cr,
            uid,
            [('shop_group_id', 'in', ids)],
            context=context
        )

    _columns = {
        'shop_group_id': fields.many2one(
            'prestashop.shop.group',
            'PrestaShop Shop Group',
            required=True,
            ondelete='cascade'
        ),
        'openerp_id': fields.many2one(
            'sale.shop',
            string='Sale Shop',
            required=True,
            readonly=True,
            ondelete='cascade'
        ),
        # what is the exact purpose of this field?
        'default_category_id': fields.many2one(
            'product.category',
            'Default Product Category',
            help="The category set on products when?? TODO."
            "\nOpenERP requires a main category on products for accounting."
        ),
        'backend_id': fields.related(
            'shop_group_id',
            'backend_id',
            type='many2one',
            relation='prestashop.backend',
            string='PrestaShop Backend',
            store={
                'prestashop.shop': (
                    lambda self, cr, uid, ids, c={}: ids,
                    ['shop_group_id'],
                    10
                ),
                'prestashop.shop.group': (
                    _get_shop_from_shopgroup,
                    ['backend_id'],
                    20
                ),
            },
            readonly=True
        ),
    }

    _sql_constraints = [
        ('prestashop_uniq', 'unique(backend_id, prestashop_id)',
         'A shop with the same ID on PrestaShop already exists.'),
    ]


class sale_shop(orm.Model):
    _inherit = 'sale.shop'

    _columns = {
        'prestashop_bind_ids': fields.one2many(
            'prestashop.shop', 'openerp_id',
            string='PrestaShop Bindings',
            readonly=True),
    }


class prestashop_res_lang(orm.Model):
    _name = 'prestashop.res.lang'
    _inherit = 'prestashop.binding'
    _inherits = {'res.lang': 'openerp_id'}

    _columns = {
        'openerp_id': fields.many2one(
            'res.lang',
            string='Lang',
            required=True,
            ondelete='cascade'
        ),
        'active': fields.boolean('Active in prestashop'),
    }

    _defaults = {
        #'active': lambda *a: False,
        'active': False,
    }

    _sql_constraints = [
        ('prestashop_uniq', 'unique(backend_id, prestashop_id)',
         'A Lang with the same ID on Prestashop already exists.'),
    ]


class res_lang(orm.Model):
    _inherit = 'res.lang'

    _columns = {
        'prestashop_bind_ids': fields.one2many(
            'prestashop.res.lang',
            'openerp_id',
            string='prestashop Bindings',
            readonly=True),
    }


class prestashop_res_country(orm.Model):
    _name = 'prestashop.res.country'
    _inherit = 'prestashop.binding'
    _inherits = {'res.country': 'openerp_id'}

    _columns = {
        'openerp_id': fields.many2one(
            'res.country',
            string='Country',
            required=True,
            ondelete='cascade'
        ),
    }

    _sql_constraints = [
        ('prestashop_uniq', 'unique(backend_id, prestashop_id)',
         'A Country with the same ID on prestashop already exists.'),
    ]


class res_country(orm.Model):
    _inherit = 'res.country'

    _columns = {
        'prestashop_bind_ids': fields.one2many(
            'prestashop.res.country',
            'openerp_id',
            string='prestashop Bindings',
            readonly=True
        ),
    }


class prestashop_res_currency(orm.Model):
    _name = 'prestashop.res.currency'
    _inherit = 'prestashop.binding'
    _inherits = {'res.currency': 'openerp_id'}

    _columns = {
        'openerp_id': fields.many2one(
            'res.currency',
            string='Currency',
            required=True,
            ondelete='cascade'
        ),
    }

    _sql_constraints = [
        ('prestashop_uniq', 'unique(backend_id, prestashop_id)',
         'A Currency with the same ID on prestashop already exists.'),
    ]


class res_currency(orm.Model):
    _inherit = 'res.currency'

    _columns = {
        'prestashop_bind_ids': fields.one2many(
            'prestashop.res.currency',
            'openerp_id',
            string='prestashop Bindings',
            readonly=True
        ),
    }


class prestashop_account_tax(orm.Model):
    _name = 'prestashop.account.tax'
    _inherit = 'prestashop.binding'
    _inherits = {'account.tax': 'openerp_id'}

    _columns = {
        'openerp_id': fields.many2one(
            'account.tax',
            string='Tax',
            required=True,
            ondelete='cascade'
        ),
    }

    _sql_constraints = [
        ('prestashop_uniq', 'unique(backend_id, prestashop_id)',
         'A Tax with the same ID on prestashop already exists.'),
    ]


class account_tax(orm.Model):
    _inherit = 'account.tax'

    _columns = {
        'prestashop_bind_ids': fields.one2many(
            'prestashop.account.tax',
            'openerp_id',
            string='prestashop Bindings',
            readonly=True
        ),
    }


class prestashop_account_tax_group(orm.Model):
    _name = 'prestashop.account.tax.group'
    _inherit = 'prestashop.binding'
    _inherits = {'account.tax.group': 'openerp_id'}

    _columns = {
        'openerp_id': fields.many2one(
            'account.tax.group',
            string='Tax Group',
            required=True,
            ondelete='cascade'
        ),
    }

    _sql_constraints = [
        ('prestashop_uniq', 'unique(backend_id, prestashop_id)',
         'A Tax Group with the same ID on prestashop already exists.'),
    ]


class account_tax_group(orm.Model):
    _inherit = 'account.tax.group'

    _columns = {
        'prestashop_bind_ids': fields.one2many(
            'prestashop.account.tax.group',
            'openerp_id',
            string='Prestashop Bindings',
            readonly=True
        ),
        'company_id': fields.many2one('res.company', 'Company', select=1, required=True),
    }
