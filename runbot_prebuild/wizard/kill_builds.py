# -*- encoding: utf-8 -*-
#
#    Module Writen to OpenERP, Open Source Management Solution
#
#    Copyright (c) 2014 Vauxoo - http://www.vauxoo.com/
#    All Rights Reserved.
#    info Vauxoo (info@vauxoo.com)
#
#    Coded by: Vauxoo Consultores (info@vauxoo.com)
#
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
from openerp.osv import osv, fields


class wizard_kill_builds(osv.osv_memory):
    _name = 'wizard.kill.builds'

    def default_get(self, cr, uid, fields_list, context=None):
        """
        Get default values
        @param self: The object pointer.
        @param cr: A database cursor
        @param uid: ID of the user currently logged in
        @param fields_list: List of fields for default value
        @param context: A standard dictionary
        @return: default values of fields
        """
        if context is None:
            context = {}
        res = super(wizard_kill_builds, self).default_get(
            cr, uid, fields_list, context=context)
        if context.get('active_ids', False):
            res.update({'build_ids': context.get('active_ids')})
        return res

    _columns = {
        'build_ids': fields.many2many(
            'runbot.build', 'wizard_kill_buids_ids', 'wizard_id', 'buids_id',
            'Builds to kill', help='This buids will killed')
    }

    def kill_builds(self, cr, uid, ids, context=None):
        '''
        Method to call method kill from runbot build
        @param self: The object pointer.
        @param cr: A database cursor
        @param uid: ID of the user currently logged in
        @param ids: Id of wizard that call this method
        @param context: A standard dictionary
        '''
        if context is None:
            context = {}
        build_obj = self.pool.get('runbot.build')
        for wiz in self.browse(cr, uid, ids, context=context):
            buids_ids = [x.id for x in wiz.build_ids]
            build_obj.kill(cr, uid, buids_ids, context=context)
        return {}
