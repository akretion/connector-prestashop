# -*- encoding: utf-8 -*-
# #############################################################################
#
#   Prestashop_catalog_manager for OpenERP
#   Copyright (C) 2012-TODAY Akretion <http://www.akretion.com>.
#   All Rights Reserved
#   @author : Sébastien BEAU <sebastien.beau@akretion.com>
#             Benoît GUILLOT <benoit.guillot@akretion.com>
#
#   This program is free software: you can redistribute it and/or modify
#   it under the terms of the GNU Affero General Public License as
#   published by the Free Software Foundation, either version 3 of the
#   License, or (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU Affero General Public License for more details.
#
#   You should have received a copy of the GNU Affero General Public License
#   along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
###############################################################################
from openerp.addons.connector.queue.job import job
from openerp.addons.connector.event import (
    on_record_create,
    on_record_write,
    on_record_unlink
)
from openerp.addons.connector.unit.mapper import (
    ExportMapper,
    mapping,
    changed_by,
    m2o_to_backend
)
from openerp.addons.prestashoperpconnect.unit.export_synchronizer import (
    TranslationPrestashopExporter,
    PrestashopExporter,
    export_record
)
from openerp.addons.prestashoperpconnect.unit.delete_synchronizer import PrestashopDeleter
from openerp.addons.prestashoperpconnect.unit.mapper import (
    TranslationPrestashopExportMapper,
    PrestashopExportMapper
)

import openerp.addons.prestashoperpconnect.consumer as prestashoperpconnect
from openerp.addons.prestashoperpconnect.unit.binder import PrestashopModelBinder
from openerp.addons.prestashoperpconnect.connector import get_environment
from openerp.addons.prestashoperpconnect.backend import prestashop
from openerp.addons.prestashoperpconnect.product import INVENTORY_FIELDS
from openerp.addons.prestashoperpconnect_catalog_manager.product_combination \
    import product_product_write
from openerp.osv import fields, orm
import openerp.addons.decimal_precision as dp


@on_record_create(model_names='prestashop.product.template')
def prestashop_product_template_create(session, model_name, record_id, vals):
    if session.context.get('connector_no_export'):
        return
    prestashoperpconnect.delay_export(session, model_name, record_id, vals)
    #Create prestashop image for each image
    record = session.env[model_name].browse(record_id)
    for image in record.image_ids:
        binding = session.env['prestashop.product.image'].create({
            'openerp_id': image.id,
            'backend_id': record.backend_id.id
            })


@on_record_write(model_names='prestashop.product.template')
def prestashop_product_template_write(session, model_name, record_id, vals):
    if session.context.get('connector_no_export'):
        return
    fields = list(set(vals).difference(set(INVENTORY_FIELDS)))
    if fields:
        prestashoperpconnect.delay_export(session, model_name, record_id, vals)


@on_record_create(model_names='prestashop.product.image')
def prestashop_product_image_create(session, model_name, record_id, vals):
    if session.context.get('connector_no_export'):
        return
# remove vals to force export when create
# TODO think of a better way
    export_record.delay(session, model_name, record_id, {}, priority=50)


@on_record_create(model_names='base_multi_image.image')
def product_image_create(session, model_name, record_id, vals):
    if session.context.get('connector_no_export'):
        return
    image = session.env[model_name].browse(record_id)
    for prestashop_product in image.product_id.prestashop_bind_ids:
        binding = session.env['prestashop.product.image'].create({
            'openerp_id': record_id,
            'backend_id': prestashop_product.backend_id.id
            })


#@on_record_create(model_names='product.template')
#def product_template_create(session, model_name, record_id, fields):
#    if session.context.get('connector_no_export'):
#        return
#    model = session.pool.get(model_name)
#    record = model.browse(session.cr, session.uid,
#                          record_id, context=session.context)
#    for binding in record.prestashop_bind_ids:
#        export_record.delay(session, 'prestashop.product.template',
#                            binding.id, fields, priority=20)


@on_record_write(model_names='product.template')
def product_template_write(session, model_name, record_id, vals):
    if session.context.get('connector_no_export'):
        return
    prestashoperpconnect.delay_export_all_bindings(
        session, model_name, record_id, vals)


@on_record_write(model_names='product.image')
def product_image_write(session, model_name, record_id, vals):
    if session.context.get('connector_no_export'):
        return
    record = session.env[model_name].browse(record_id)
    for binding in record.prestashop_bind_ids:
        if prestashoperpconnect.need_to_export(
                session, binding._model._name, binding.id,
                backend_id=binding.backend_id.id, fields=vals):
            export_record.delay(
                session, binding._model._name, binding.id, priority=50)


@on_record_unlink(model_names='product.image')
def product_image_unlink(session, model_name, record_id):
    if session.context.get('connector_no_export'):
        return
    record = session.env[model_name].browse(record_id)
    for binding in record.prestashop_bind_ids:
        product_id = session.env['prestashop.product.template'].search([
            ('backend_id', '=', binding.backend_id.id),
            ('openerp_id', '=', binding.product_id.id)])
        env = get_environment(
            session, binding._model._name, binding.backend_id.id)
        binder = env.get_connector_unit(PrestashopModelBinder)
        ext_id = binder.to_backend(binding.id)
        ext_product_id = product_id.prestashop_id
        if ext_id:
            export_delete_image.delay(
                session, binding._model._name, binding.backend_id.id,
                ext_id, ext_product_id)


#@on_record_write(model_names='product.product')
#def product_product_write(session, model_name, record_id, fields):
#    if session.context.get('connector_no_export'):
#        return
#    model = session.pool.get(model_name)
#    record = model.browse(session.cr, session.uid,
#                          record_id, context=session.context)
#    for binding in record.product_tmpl_id.prestashop_bind_ids:
#        export_record.delay(session, 'prestashop.product.template',
#                            binding.id, fields, priority=20)


class prestashop_product_template(orm.Model):
    _inherit = 'prestashop.product.template'

    _columns = {
        'meta_title': fields.char(
            'Meta Title',
            translate=True
        ),
        'meta_description': fields.char(
            'Meta Description',
            translate=True
        ),
        'meta_keywords': fields.char(
            'Meta Keywords',
            translate=True
        ),
        'tags': fields.char(
            'Tags',
            translate=True
        ),
         'available_for_order': fields.boolean(
             'Available For Order'
         ),
        'show_price': fields.boolean(
            'Show Price'
        ),
        'online_only': fields.boolean(
            'Online Only'
        ),
        'additional_shipping_cost': fields.float(
            'Additional Shipping Price',
            digits_compute=dp.get_precision('Product Price'),
            help="Additionnal Shipping Price for the product on Prestashop"),
        'available_now': fields.char(
            'Available Now',
            translate=True
        ),
        'available_later': fields.char(
            'Available Later',
            translate=True
        ),
        'available_date': fields.date(
            'Available Date'
        ),
        'minimal_quantity': fields.integer(
            'Minimal Quantity',
            help='Minimal Sale quantity',
        ),
    }

    _defaults = {
        'minimal_quantity': 1,
    }


@prestashop
class ProductTemplateExport(TranslationPrestashopExporter):
    _model_name = 'prestashop.product.template'

    def _export_dependencies(self):
        """ Export the dependencies for the product"""
        #TODO add export of category
        #Comprobamos si los atributos y valores estan creados
        attribute_binder = self.binder_for(
            'prestashop.product.combination.option')
        option_binder = self.binder_for(
            'prestashop.product.combination.option.value')
        combination_binder = self.binder_for(
            'prestashop.product.combination')
        attribute_obj = self.session.pool[
            'prestashop.product.combination.option']
        for line in self.erp_record.attribute_line_ids:
            attribute_ext_id = attribute_binder.to_backend(
                line.attribute_id.id, wrap=True)
            if not attribute_ext_id:
                ctx = self.session.context.copy()
                ctx['connector_no_export'] = True
                res = {
                    'backend_id': self.backend_record.id,
                    'openerp_id': line.attribute_id.id,
                }
                self.session.change_context({'connector_no_export': True})
                attribute_ext_id = attribute_obj.create(self.session.cr,
                                                        self.session.uid,
                                                        res)
                export_record(
                    self.session,
                    'prestashop.product.combination.option',
                    attribute_ext_id)
            for value in line.value_ids:
                value_ext_id = option_binder.to_backend(value.id, wrap=True)
                if not value_ext_id:
                    ctx = self.session.context.copy()
                    ctx['connector_no_export'] = True
                    value_ext_id = self.session.pool[
                        'prestashop.product.combination.option.value'].create(
                        self.session.cr, self.session.uid, {
                        'backend_id': self.backend_record.id,
                        'openerp_id': value.id}, context=ctx)
                    export_record(
                        self.session,
                        'prestashop.product.combination.option.value',
                        value_ext_id
                    )
            # comprobar si tiene variantes

#        #Comprobamos si las combinaciones estan creadas
        if self.erp_record.product_variant_ids:
            for product in self.erp_record.product_variant_ids:
                if not product.attribute_value_ids:
                    continue
                combination_ext_id = combination_binder.to_backend(
                    product.id, wrap=True)
                if not combination_ext_id:
                    ctx = self.session.context.copy()
                    ctx['connector_no_export'] = True
                    combination_ext_id = self.session.pool[
                        'prestashop.product.combination'].create(
                        self.session.cr, self.session.uid, {
                            'backend_id': self.backend_record.id,
                            'openerp_id': product.id,
                            'main_template_id': self.binding_id}, context=ctx)
                    export_record(self.session,
                                  'prestashop.product.combination',
                                  combination_ext_id)

    def _after_export(self):
        if self.erp_record.product_variant_ids:
            combination_binder = self.binder_for(
            'prestashop.product.combination')
            for product in self.erp_record.product_variant_ids:
                combination_ext_id = combination_binder.to_backend(
                    product.id, wrap=True)
                if combination_ext_id:
                    product_product_write(self.session, 'product.product',
                                          product.id, {})


@prestashop
class ProductTemplateExportMapper(TranslationPrestashopExportMapper):
    _model_name = 'prestashop.product.template'

    direct = [
        ('list_price', 'price'),
        ('available_for_order', 'available_for_order'),
        ('show_price', 'show_price'),
        ('online_only', 'online_only'),
        ('weight', 'weight'),
        ('standard_price', 'wholesale_price'),
        ('default_code', 'reference'),
        ('default_shop_id', 'id_shop_default'),
        ('always_available', 'active'),
        ('ean13', 'ean13'),
        ('additional_shipping_cost', 'additional_shipping_cost'),
        ('minimal_quantity', 'minimal_quantity'),
        ('available_date', 'available_date'), #check date format
#        (m2o_to_backend('categ_id', binding='prestashop.product.category'),
#         'id_category_default'),
    ]

#    translatable_fields = [
#        ('name', 'name'),
#        ('link_rewrite', 'link_rewrite'),
#        ('meta_title', 'meta_title'),
#        ('meta_description', 'meta_description'),
#        ('meta_keywords', 'meta_keywords'),
#        ('tags', 'tags'),
#        ('description_short_html', 'description_short'),
#        ('description_html', 'description'),
#        ('available_now', 'available_now'),
#        ('available_later', 'available_later'),
#    ]

    def _get_template_feature(self, record):
        #Buscar las product.attribute y sus valores para asociarlos al producto
        template_feature = []
        attribute_binder = self.binder_for(
            'prestashop.product.combination.option')
        option_binder = self.binder_for(
            'prestashop.product.combination.option.value')
        for line in record.attribute_line_ids:
            feature_dict = {}
            attribute_ext_id = attribute_binder.to_backend(
                line.attribute_id.id, wrap=True)
            if not attribute_ext_id:
                continue
            feature_dict = {'id': attribute_ext_id, 'custom': ''}
            values_ids = []
            for value in line.value_ids:
                value_ext_id = option_binder.to_backend(value.id,
                                                        wrap=True)
                if not value_ext_id:
                    continue
                values_ids.append(value_ext_id)
            res = {'id_feature_value': values_ids}
            feature_dict.update(res)
            template_feature.append(feature_dict)
        return template_feature

    def _get_product_category(self, record):
        ext_categ_ids = []
        binder = self.binder_for('prestashop.product.category')
        categories = list(set(record.categ_ids + record.categ_id))
        for category in categories:
            ext_id = binder.to_backend(category.id, wrap=True)
            if ext_id:
                ext_categ_ids.append({'id': ext_id})
        return ext_categ_ids

    def _get_product_links(self, record):
        links = []
        binder = self.binder_for('prestashop.product.template')
        for link in record.product_link_ids:
            ext_id = binder.to_backend(link.linked_product_id.id, wrap=True)
            if ext_id:
                links.append({'id': ext_id})
        return links


    #TODO changed by attribute_line_ids.value_ids
    @changed_by(
        'attribute_line_ids', 'categ_ids', 'categ_id', 'product_link_ids'
    )
    @mapping
    def associations(self, record):
        return {
            'associations': {
                'categories': {
                    'category_id': self._get_product_category(record)},
                'product_features': {
                    'product_feature': self._get_template_feature(record)},
                'accessories': {
                    'accessory': self._get_product_links(record)},
            }
        }

    @changed_by('taxes_id')
    @mapping
    def tax_ids(self, record):
        if record.taxes_id:
            binder = self.binder_for('prestashop.account.tax.group')
            ext_id = binder.to_backend(record.taxes_id[0].group_id.id, wrap=True)
            return {'id_tax_rules_group': ext_id}
        return {}

    @changed_by(
        'name', 'link_rewrite', 'meta_title', 'meta_description',
        'meta_keywords', 'tags', 'description_short_html', 'description_html',
        'available_now', 'available_later'
    )
    @mapping
    def translatable_fields(self, record):
        translatable_fields = [
        ('name', 'name'),
        ('link_rewrite', 'link_rewrite'),
        ('meta_title', 'meta_title'),
        ('meta_description', 'meta_description'),
        ('meta_keywords', 'meta_keywords'),
        ('tags', 'tags'),
        ('description_short_html', 'description_short'),
        ('description_html', 'description'),
        ('available_now', 'available_now'),
        ('available_later', 'available_later'),
        ]
        trans = self.unit_for(TranslationPrestashopExporter)
        translated_fields = self.convert_languages(
            trans.get_record_by_lang(record.id), translatable_fields)
        return translated_fields


@prestashop
class ProductImageExporter(PrestashopExporter):
    _model_name = 'prestashop.product.image'


@prestashop
class ProductImageDeleter(PrestashopDeleter):
    _model_name = 'prestashop.product.image'

    def run(self, external_id, product_id):
        self.backend_adapter.delete(external_id, product_id)
        return ('Image %s of the product %s deleted on Prestashop') % (external_id, product_id)


@prestashop
class ProductImageExportMapper(PrestashopExportMapper):
    _model_name = 'prestashop.product.image'

#    direct = [
#        ('image', 'image')
#        ]

    @changed_by('image')
    @mapping
    def image(self, record):
        return {'image': record['image']}

    @changed_by('name', 'extension2')
    @mapping
    def filename(self, record):
        return {'filename': record['name'] + record['extension2']}

    @changed_by('product_id')
    @mapping
    def product_id(self, record):
        binder = self.binder_for('prestashop.product.template')
        ext_product_id = binder.to_backend(record.product_id.id, wrap=True)
        return {'product_id': ext_product_id}

@job
def export_delete_image(session, model_name, backend_id, external_id, product_id):
    """ Delete a record on Prestashop """
    env = get_environment(session, model_name, backend_id)
    deleter = env.get_connector_unit(ProductImageDeleter)
    return deleter.run(external_id, product_id)
