# -*- encoding: utf-8 -*-
###############################################################################
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
from openerp.osv import fields, orm
from openerp.addons.connector.event import on_record_create, on_record_write
from openerp.addons.prestashoperpconnect.unit.export_synchronizer import (
    export_record,
    TranslationPrestashopExporter,
    PrestashopExporter,
    )

#from openerp.addons.prestashoperpconnect.unit.binder import \
    #PrestashopModelBinder
from openerp.addons.prestashoperpconnect.unit.mapper import (
    TranslationPrestashopExportMapper,
    PrestashopExportMapper
)

from openerp.addons.prestashoperpconnect.backend import prestashop
from openerp.addons.prestashoperpconnect.unit.backend_adapter import \
    GenericAdapter
from openerp.addons.connector.exception import InvalidDataError
from openerp.addons.connector.unit.mapper import mapping, changed_by
import openerp.addons.prestashoperpconnect.consumer as prestashoperpconnect


#class prestashop_product_combination_option_value(orm.Model):
#    _name = 'prestashop.product.combination.option.value'
#
#
#    def create(self, cr, uid, vals, context=None):
#        prest_attribute_obj = \
#            self.pool['prestashop.product.combination.option.value']
#        attribute_option_obj = \
#            self.pool['product.attribute.value']
#        option = attribute_option_obj.read(cr, uid, vals['openerp_id'],
#                                           ['attribute_id'], context=context)
#        if option and option['attribute_id']:
#            prestashop_attribute_ids = prest_attribute_obj.search(
#                cr, uid, [('backend_id', '=', vals['backend_id']),
#                          ('openerp_id', '=', option['attribute_id'][0])],
#                context=context)
#            if prestashop_attribute_ids:
#                vals['prestashop_product_attribute_id'] = \
#                    prestashop_attribute_ids[0]
#                return super(prestashop_product_combination_option_value,
#                             self).create(cr, uid, vals, context=context)
#        raise InvalidDataError("You have to export the product attribute "
#                               "before the attribute option !")
#        return True


@on_record_create(model_names='prestashop.product.combination.option')
@on_record_write(model_names='prestashop.product.combination.option')
@on_record_write(model_names='prestashop.product.combination.option.value')
def delay_export_attibutes(session, model_name, record_id, vals):
    if session.context.get('connector_no_export'):
        return
    prestashoperpconnect.delay_export(session, model_name, record_id, vals)


@on_record_create(model_names='prestashop.product.combination.option.value')
def option_value_create(session, model_name, record_id, vals):
    if session.context.get('connector_no_export'):
        return
#remove vals to force export for create
    prestashoperpconnect.delay_export(session, model_name, record_id, {})


@on_record_write(model_names='product.attribute')
@on_record_write(model_names='product.attribute.value')
def delay_export_binding_attributes(session, model_name, record_id, vals):
    if session.context.get('connector_no_export'):
        return
    prestashoperpconnect.delay_export_all_bindings(
        session, model_name, record_id, vals)


@on_record_create(model_names='product.attribute.value')
def attribute_option_create(session, model_name, record_id, vals):
    if session.context.get('connector_no_export'):
        return
    record = session.env[model_name].browse(record_id)
    prestashop_model_name = 'prestashop.product.combination.option.value'
    for prestashop_attribute in record.attribute_id.prestashop_bind_ids:
        binding = session.env[prestashop_model_name].create({
            'openerp_id': record_id,
            'backend_id': prestashop_attribute.backend_id.id})


#@prestashop
#class ProductAttributeAdapter(GenericAdapter):
#    _model_name = 'prestashop.product.combination.option'
#    _prestashop_model = 'product_features'
#    _export_node_name = 'product_feature'
#
#
#@prestashop
#class AttributeOptionAdapter(GenericAdapter):
#    _model_name = 'prestashop.product.combination.option.value'
#    _prestashop_model = 'product_feature_values'
#    _export_node_name = 'product_feature_value'


#@prestashop
#class PrestashopProductAttributeBinder(PrestashopModelBinder):
#    _model_name = 'prestashop.product.combination.option'


#@prestashop
#class PrestashopAttributeOptionBinder(PrestashopModelBinder):
#    _model_name = 'prestashop.product.combination.option.value'

@prestashop
class ProductCombinationOptionExport(PrestashopExporter):
    _model_name = 'prestashop.product.combination.option'


@prestashop
class ProductCombinationOptionExportMapper(TranslationPrestashopExportMapper):
    _model_name = 'prestashop.product.combination.option'

    direct = [
        ('prestashop_position', 'position'),
        ('group_type','group_type')
        #('name', 'name'),
    ]

    @changed_by('name', 'public_name')
    @mapping
    def translatable_fields(self, record):
        translatable_fields = [
        ('name', 'name'),
        ('public_name', 'public_name')
                              ]
        trans = TranslationPrestashopExporter(self.environment)
        translated_fields = self.convert_languages(trans.get_record_by_lang(record.id),translatable_fields)
        return translated_fields


@prestashop
class ProductCombinationOptionValueExporter(PrestashopExporter):
    _model_name = 'prestashop.product.combination.option.value'

    def _export_dependencies(self):
        """ Export the dependencies for the record"""
        attribute_id = self.erp_record.attribute_id.id
        # export product attribute
        binder = self.get_binder_for_model(
            'prestashop.product.combination.option')
        if not binder.to_backend(attribute_id, wrap=True):
            exporter = self.get_connector_unit_for_model(
                TranslationPrestashopExporter,
                'prestashop.product.combination.option')
            exporter.run(attribute_id)
        return


@prestashop
class ProductCombinationOptionValueExportMapper(TranslationPrestashopExportMapper):
    _model_name = 'prestashop.product.combination.option.value'

    direct = [('name', 'value')]
    #translatable_fields = [('name', 'value')]

    @changed_by('attribute_id')
    @mapping
    def prestashop_product_attribute_id(self, record):
        attribute_binder = self.binder_for(
            'prestashop.product.combination.option.value')
        return {
            'id_feature': attribute_binder.to_backend(record.attribute_id.id,
                                                      wrap=True)
        }

    @changed_by('attribute_id')
    @mapping
    def prestashop_product_group_attribute_id(self, record):
        attribute_binder = self.binder_for(
            'prestashop.product.combination.option')
        return {
            'id_attribute_group': attribute_binder.to_backend(
                record.attribute_id.id, wrap=True)
        }

    @changed_by('name')
    @mapping
    def translatable_fields(self, record):
        translatable_fields = [
            ('name', 'name'),
        ]
        trans = TranslationPrestashopExporter(self.environment)
        translated_fields = self.convert_languages(trans.get_record_by_lang(record.id),translatable_fields)
        return translated_fields
