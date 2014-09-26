#!/usr/bin/python
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
"""
    This file added the models and methods to can create runbots prebuild
    and with this create builds.
"""
from openerp.osv import fields, osv
import logging
import time
import werkzeug
from collections import OrderedDict

from openerp.addons.runbot.runbot import RunbotController
from openerp.addons.runbot.runbot import flatten
from openerp.http import request
from openerp import http
from openerp.addons.website.models.website import slug
from openerp.addons.website_sale.controllers.main import QueryURL
from openerp import SUPERUSER_ID
from openerp import tools
import urllib

_logger = logging.getLogger(__name__)

REFS_FETCH_DEFAULT = [
    '+refs/heads/*:refs/heads/*', '+refs/pull/*/head:refs/pull/*']
REFS_GET_DATA = ['refs/heads', 'refs/pull']


class runbot_prebuild_branch(osv.osv):

    '''
    Object used to relate runbot prebuilds with runbot branch, this as lines
    of runbot prebuild.
    '''
    _name = "runbot.prebuild.branch"
    _rec_name = 'branch_id'

    _columns = {
        'branch_id': fields.many2one('runbot.branch', 'Branch', required=True,
                                     ondelete='cascade', select=1),
        'check_pr': fields.boolean('Check PR',
            help='If is True, this will check Pull Request for this branch '
            'in this prebuild', copy=False),
        'check_new_commit': fields.boolean('Check New Commit',
            help='If is True, this will check new commit for this branch in '
            'this prebuild and will make a new build.', copy=False),
        'sha': fields.char('SHA commit', size=40,
            help='Empty=Currently version\nSHA=Get this version in builds'),
        'prebuild_id': fields.many2one('runbot.prebuild', 'Pre-Build',
            required=True, ondelete='cascade', select=1),
        'repo_id': fields.related('branch_id', 'repo_id', type="many2one",
            relation="runbot.repo", string="Repository", readonly=True,
            store=True, ondelete='cascade', select=1),
    }


class runbot_team(osv.Model):

    '''
    Object used to create team to work in runbot
    '''

    _name = 'runbot.team'

    _columns = {
        'name': fields.char('Name', help='Name of the team'),
        'description': fields.text('Desciption',
                                   help='A little description of the team'),
        'privacy_visibility': fields.selection(
            [('public', 'Public'),
             ('private', 'Private')], 'Privacy Visibility'),
    }


class runbot_build(osv.osv):
    _inherit = "runbot.build"

    def _get_url_name_id(self, cr, uid, ids, fields, name, args,
                         context=None):
        '''
        Documentation TODO
        @param cr: A database cursor
        @param uid: ID of the user currently logged in
        @param ids: list of ids for which name should be read
        @param fields: TODO
        @param name: TODO
        @param args: TODO
        @param context: context arguments, like lang, time zone
        '''
        if context is None:
            context = {}
        res = {}

        for build in self.browse(cr, uid, ids, context=context):
            res[build.id] = False

            if build.prebuild_id and build.prebuild_id:
                url_parse = urllib.quote(build.author or '')
            else:
                url_parse = urllib.quote(build.branch_id.branch_name or '')
            url_parse = url_parse.replace('.', '_').replace('/', '_')
            res[build.id] = url_parse
        return res

    _columns = {
        'name_id': fields.function(_get_url_name_id,
                                   string='URL Name ID',
                                   type='char',
                                   help='Contains the id for create href'),
    }


class runbot_prebuild(osv.osv):
    '''
    Object used to create a prebuild, and convert this in a build
    '''
    _name = "runbot.prebuild"

    _columns = {
        'name': fields.char("Name", size=128, required=True),
        'team_id': fields.many2one('runbot.team', 'Team',
            help='Team of work', copy=True, required=True),
        'module_branch_ids': fields.one2many('runbot.prebuild.branch',
            'prebuild_id', string='Branches of modules', copy=True,
            help="Community addons branches which need to run tests."),
        'sticky': fields.boolean('Sticky', select=1,
            help='If True: Stay alive a instance ever. And check PR to main'
            ' branch and modules branches for make pre-builds\nIf False: '
            'Stay alive a instance only moment and not check PR.', copy=False),
        'modules': fields.char("Modules to Install",
            help="Empty is all modules availables", copy=True),
        'lang': fields.selection(tools.scan_languages(), 'Language',
            help='Language to change instance after of run test.', copy=True),
        'pylint_config': fields.many2one('pylint.conf',
            string='Pylint Config'),
        'modules_to_exclude': fields.char("Modules to exclude",
            help='Empty is exclude none. Add modules is exclude this one. '\
            'FEATURE TODO', copy=True),
        'script_prebuild': fields.text('Script Pre-Build',
            help="Script to execute before run build", copy=True),
        'script_posbuild': fields.text('Script Pos-Build',
            help="Script to execute after run build", copy=True),
        'prebuild_parent_id': fields.many2one('runbot.prebuild',
            'Parent Prebuild', copy=True,
            help='If this is a prebuild from PR this field is for set '
            'original prebuild'),
    }

    _defaults = {
        'sticky': False,
    }
    # TODO: Add constraint that add prebuild_lines of least one main repo type
    # TODO: Add related to repo.type store=True

    def get_builds_prebuild(self, cr, uid, ids, context=None):
        '''
        Method to get the builds that have been generated with this prebuild
        @params
        @param cr: A database cursor
        @param uid: ID of the user currently logged in
        @param ids: list of ids for which name should be read
        @param context: context arguments, like lang, time zone
        '''
        if context is None:
            context = {}
        build_obj = self.pool.get('runbot.build')
        build_ids = build_obj.search(
            cr, uid, [('prebuild_id', 'in', ids)], context=context)
        return {
            'name': 'Prebuild Origin',
            'res_model': 'runbot.build',
            'view_type': 'form',
            'view_mode': 'tree,form',
            'type': 'ir.actions.act_window',
            'domain': [('id', 'in', build_ids)],
        }

    def write(self, cr, uid, ids, values, context=None):
        """
        if update data prebuild then detect it in builds
        This will create a new build with new change 
        into new_pr and main_build function.
        """
        if isinstance(ids, (long, int)):
            ids = [ids]
        if ids and values:
            build_pool = self.pool.get('runbot.build')
            build_ids = build_pool.search(cr, uid, [
                    ('prebuild_id', 'in', ids),
                ], context=context)
            if build_ids:
                build_to_kill_ids = build_pool.search(cr, uid, [
                    ('id', 'in', build_ids),
                    ('state', '<>', 'done'),
                ], context=context)
                build_pool.kill(cr, uid, build_to_kill_ids, context=context)
                build_pool.write(cr, uid, build_ids, {
                        'change_prebuild_ok': True,
                    }, context=context)
        return super(runbot_prebuild, self).write(cr, uid, ids,
            values, context=context)

    def create_prebuild_new_commit(self, cr, uid, ids, context=None):
        """
        Create new build with changes in your branches with
        check_new_commit=True
        @params
        @param cr: A database cursor
        @param uid: ID of the user currently logged in
        @param ids: list of ids for which name should be read
        @param context: context arguments, like lang, time zone
        """
        build_pool = self.pool.get('runbot.build')
        build_line_pool = self.pool.get('runbot.build.line')
        repo_pool = self.pool.get('runbot.repo')
        branch_pool = self.pool.get('runbot.branch')
        build_new_ids = []
        for prebuild_id in ids:
            build_ids = build_pool.search(cr, uid, [
                ('prebuild_id', 'in', [prebuild_id]),
                ('from_main_prebuild_ok', '=', True),
                ('change_prebuild_ok', '<>', True),
            ], context=context)
            if not build_ids:
                # If not build exists then create it and mark as
                # from_main_prebuild_ok=True
                build_new_id = self.create_build(cr, uid, [prebuild_id],
                    default_data={
                        'from_main_prebuild_ok': True}, context=context)
                build_new_ids.append(build_new_id)
                continue

            build_line_ids = build_line_pool.search(cr, uid, [
                ('build_id', 'in', build_ids),
                ('prebuild_line_id.check_new_commit', '=', True),
            ], context=context)
            if build_line_ids:
                # Get all branches from build_line of this prebuild_sticky
                build_line_datas = build_line_pool.read(
                    cr, uid, build_line_ids, ['branch_id'], context=context)
                branch_ids = list(
                    set([r['branch_id'][0] for r in build_line_datas]))
                # Get last commit and search it as sha of build line
                for branch in branch_pool.browse(cr, uid, branch_ids,
                                                 context=context):
                    _logger.info("get last commit info for check new commit")
                    refs = repo_pool.get_ref_data(
                        cr, uid, [branch.repo_id.id], branch.name,
                        fields=['objectname'], context=context)
                    if refs and refs[branch.repo_id.id]:
                        ref_data = refs[branch.repo_id.id][0]
                        sha = ref_data['objectname']
                        build_line_with_sha_ids = build_line_pool.search(cr,
                            uid, [('branch_id', '=', branch.id),
                                ('build_id', 'in', build_ids),
                                ('sha', '=', sha)], context=context, limit=1)
                        if not build_line_with_sha_ids:
                            # If not last commit then create build with last
                            # commit
                            replace_branch_info = {
                                branch.id: {'reason_ok': True}}
                            default_data = {'from_main_prebuild_ok': True}
                            build_new_id = self.create_build(cr, uid, [
                                prebuild_id], default_data=default_data,
                                replace_branch_info=replace_branch_info,
                                context=context)
                            build_new_ids.append(build_new_id)
        return build_new_ids

    def create_build_pr(self, cr, uid, ids, context=None):
        """
        Create build from pull request with build line check_pr=True
        @params
        @param cr: A database cursor
        @param uid: ID of the user currently logged in
        @param ids: list of ids for which name should be read
        @param context: context arguments, like lang, time zone
        """
        new_build_ids = []
        branch_pool = self.pool.get('runbot.branch')
        build_line_pool = self.pool.get('runbot.build.line')
        for prebuild in self.browse(cr, uid, ids, context=context):
            for prebuild_line in prebuild.module_branch_ids:
                if prebuild_line.check_pr:
                    branch_pr_ids = branch_pool.search(
                        cr, uid, [
                            ('branch_base_id', '=',
                            prebuild_line.branch_id.id)], context=context)
                    # Get build of this prebuild
                    for branch_pr in branch_pool.browse(cr, uid,
                        branch_pr_ids, context=context):
                        build_line_pr_ids = build_line_pool.search(cr, uid, [
                            ('branch_id', '=', branch_pr.id),
                            ('build_id.prebuild_id', '=', prebuild.id),
                            ('build_id.change_prebuild_ok', '<>', True),
                        ], context=context)
                        if build_line_pr_ids:
                            # if exist build of pr no create new one
                            continue

                        # If not exist build of this pr then create one
                        replace_branch_info = {prebuild_line.branch_id.id: {
                            'branch_id': branch_pr.id,
                            'reason_ok': True,
                        }}
                        new_name = prebuild.name + \
                            ' [' + branch_pr.complete_name + ']'
                        build_created_ids = self.create_build(cr, uid,
                            [prebuild.id], default_data={
                            # Only for group by in qweb view
                            'branch_id': branch_pr.id,
                            'name': new_name,
                            'author': new_name,  # TODO: Get this value.
                            'subject': new_name,  # TODO: Get this value
                            }, replace_branch_info=replace_branch_info,
                            context=context)
                        new_build_ids.extend(build_created_ids)
        return new_build_ids

    def create_main_build(self, cr, uid, ids, context=None):
        """
        Use it for send default data when use button directly
        @params
        @param cr: A database cursor
        @param uid: ID of the user currently logged in
        @param ids: list of ids for which name should be read
        @param context: context arguments, like lang, time zone
        """
        default_data = {
            'from_main_prebuild_ok': True,
        }
        return self.create_build(cr, uid, ids, default_data=default_data,
            context=context)

    def create_build(self, cr, uid, ids, default_data=None,
        replace_branch_info=None, context=None):
        """
        Create a new build from a prebuild.
        @replace_branch_info: Get a dict data for replace a old branch for
        new one.
        {branch_old_id: {'branch_id': integer, 'reason_ok': boolean}}
        @params
        @param cr: A database cursor
        @param uid: ID of the user currently logged in
        @param ids: list of ids for which name should be read
        @param context: context arguments, like lang, time zone
        @param default_data: TODO
        @param replace_branch_info: TODO
        """
        if context is None:
            context = {}
        if replace_branch_info is None:
            replace_branch_info = {}
        if default_data is None:
            default_data = {}
        build_obj = self.pool.get('runbot.build')
        build_ids = []
        for prebuild in self.browse(cr, uid, ids, context=context):
            # Update repository but no create default build
            # Get build_line current info
            build_line_datas = []
            for prebuild_line in prebuild.module_branch_ids:
                new_branch_info = replace_branch_info.get(
                    prebuild_line.branch_id.id, {}) or {}
                branch_id = new_branch_info.pop(
                    'branch_id', False) or prebuild_line.branch_id.id
                new_branch_info.update({
                    'branch_id': branch_id,
                    'prebuild_line_id': prebuild_line.id,
                })

                build_line_datas.append((0, 0, new_branch_info))

            build_info = {
                # Any branch. Useless. Last of for. TODO: Use a dummy branch
                # for not affect normal process.
                'branch_id': prebuild_line.branch_id.id,
                'name': prebuild.name,
                'author': prebuild.name,  # TODO: Get this value
                'subject': prebuild.name,  # TODO: Get this value
                # TODO: Get this value
                'date': time.strftime("%Y-%m-%d %H:%M:%S"),
                'modules': prebuild.modules,
                # Important field for custom build and custom checkout
                'prebuild_id': prebuild.id,
                'team_id': prebuild and prebuild.team_id and\
                    prebuild.team_id.id or False,
                'line_ids': build_line_datas,
                'lang': prebuild.lang,
                'pylint_config': prebuild.pylint_config and\
                    prebuild.pylint_config.id or False,
            }
            build_info.update(default_data or {})
            _logger.info(
                "Create new build from prebuild_id [%s] " % (prebuild.name))
            build_id = build_obj.create(cr, uid, build_info, context=context)
            build_obj.fetch_build_lines(cr, uid, [build_id], context=context)
            build_ids.append(build_id)
        return build_ids

class runbot_build_line(osv.osv):
    '''
    '''
    _name = 'runbot.build.line'
    _rec_name = 'sha'

    def fetch_build_line(self, cr, uid, ids, context=None):
        '''
        Documentation TODO
        @param cr: A database cursor
        @param uid: ID of the user currently logged in
        @param ids: list of ids for which name should be read
        @param context: context arguments, like lang, time zone
        '''
        if context is None:
            context = {}
        for line in self.browse(cr, uid, ids, context=context):
            refs = line.repo_id.get_ref_data(line.branch_id.name,
                fields=['refname', 'objectname', 'committerdate:iso8601',
                        'authorname', 'subject', 'committername'],
                rename_fields=['refname', 'sha', 'date', 'author', 'subject',
                    'committername'], context=context)[line.repo_id.id][0]
            if line.sha and line.sha != refs['sha']:
                refs.update({'reason_ok': True})
            self.write(cr, uid, [line.id], refs, context=context)
        return True

    def _get_url_commit(self, cr, uid, ids, fields, name, args, context=None):
        '''
        Documentation TODO
        @param cr: A database cursor
        @param uid: ID of the user currently logged in
        @param ids: list of ids for which name should be read
        @param fields: TODO
        @param name: TODO
        @param args: TODO
        @param context: context arguments, like lang, time zone
        '''
        if context is None:
            context = {}
        res = {}
        for line in self.browse(cr, uid, ids, context=context):
            repo = line.repo_id
            url = False
            if line.sha:
                if repo.host_driver == 'github':
                    url = repo.url + '/commit/' + line.sha
                elif repo.host_driver == 'bitbucket':
                    url = repo.url + '/commits/' + line.sha
            res[line.id] = url
        return res

    def _get_short_commit(self, cr, uid, ids, fields, name, args,
        context=None):
        '''
        Documentation TODO
        @param cr: A database cursor
        @param uid: ID of the user currently logged in
        @param ids: list of ids for which name should be read
        @param fields: TODO
        @param name: TODO
        @param args: TODO
        @param context: context arguments, like lang, time zone
        '''
        if context is None:
            context = {}
        res = {}
        for prebuild_line in self.browse(cr, uid, ids, context=context):
            res[prebuild_line.id] = False
            if prebuild_line.sha:
                res[prebuild_line.id] = len(prebuild_line.sha) > 7 and \
                    prebuild_line.sha[:7] or prebuild_line.sha
        return res

    _columns = {
        'build_id': fields.many2one('runbot.build', 'Build', required=True,
                                    ondelete='cascade', select=1),
        'prebuild_line_id': fields.many2one('runbot.prebuild.branch',
            'Prebuild Line', required=False, ondelete='set null', select=1),
        'branch_id': fields.many2one('runbot.branch', 'Branch', required=True,
                                     ondelete='cascade', select=1),
        'refname': fields.char('Ref Name'),
        'sha': fields.char('SHA commit', size=40,
                           help='Version of commit or sha', required=False),
        'date': fields.datetime('Commit date'),
        'author': fields.char('Author'),
        'commit_url': fields.function(_get_url_commit, string='Commit URL',
            type='char', help='URL of last commit for this branch'),
        'subject': fields.text('Subject'),
        'committername': fields.char('Committer'),
        'repo_id': fields.related('branch_id', 'repo_id', type="many2one",
            relation="runbot.repo", string="Repository", readonly=True,
            store=True, ondelete='cascade', select=1),
        'reason_ok': fields.boolean('Reason',
            help='This line is the reason of create the complete build.'\
            '\nReason of PR or reason of new commit.', copy=False),
        'short_sha': fields.function(_get_short_commit, string='Short Commit',
            type='char', help='Sha short commit. Last 7 chars'),
    }

class RunbotController(RunbotController):
    '''
    Documentation TODO
    '''

    @http.route(['/runbot',
                 '/runbot/repo/<model("runbot.repo"):repo>',
                 '/runbot/team/<model("runbot.team"):team>'],
                type='http', auth="public", website=True)
    def repo(self, repo=None, team=None, search='', limit='100', refresh='',
        **post):
        '''
        Documentation TODO
        @params
        @param repo: TODO
        @param team: TODO
        @param search: TODO
        @param limit: TODO
        @param refresh: TODO
        @param post: TODO
        '''
        registry, cr, uid = request.registry, request.cr, SUPERUSER_ID
        res = super(RunbotController, self).repo(
            repo=repo, search=search, limit=limit, refresh=refresh, **post)

        branch_obj = registry['runbot.branch']
        build_obj = registry['runbot.build']
        team_obj = registry['runbot.team']
        team_ids = team_obj.search(cr, uid, [], order='id')
        teams = team_obj.browse(cr, uid, team_ids)
        res.qcontext.update({'teams': teams})
        if team:
            filters = {
                key: post.get(key, '1') for key in\
                    ['pending', 'testing', 'running', 'done']}
            build_by_branch_ids = {}

            if True:  # build_ids:
                build_query = """SELECT group_vw.*
                    FROM (
                        SELECT bu_row.branch_id, bu_row.branch_dependency_id, bu_row.prebuild_id,
                            max(case when bu_row.row_number = 1 then bu_row.build_id end) AS build1,
                            max(case when bu_row.row_number = 2 then bu_row.build_id end) AS build2,
                            max(case when bu_row.row_number = 3 then bu_row.build_id end) AS build3,
                            max(case when bu_row.row_number = 4 then bu_row.build_id end) AS build4
                        FROM (
                            SELECT bu.prebuild_id, bu.branch_id, bu.branch_dependency_id,
                                row_number() OVER(
                                        PARTITION BY bu.prebuild_id, bu.branch_id, bu.branch_dependency_id
                                        ORDER BY bu.sequence DESC, bu.id DESC
                                         ) AS row_number,
                                bu.id AS build_id
                            FROM runbot_build bu
                            WHERE team_id = %s
                        ) bu_row
                        WHERE bu_row.row_number <= 4
                        GROUP BY bu_row.prebuild_id, bu_row.branch_id, bu_row.branch_dependency_id
                    ) group_vw
                    INNER JOIN runbot_build bu1
                       ON bu1.id = build1
                    INNER JOIN runbot_branch br
                       ON br.id = group_vw.branch_id
                    ORDER BY bu1.from_main_prebuild_ok DESC, br.sticky DESC, group_vw.prebuild_id ASC, group_vw.branch_dependency_id ASC, bu1.id DESC
                    LIMIT %s
                """
                cr.execute(build_query, (team.id, int(limit)))
                build_by_branch_ids = OrderedDict(
                    [((rec[0], rec[1], rec[2]), [r for r in rec[3:] if r is\
                        not None]) for rec in cr.fetchall()])

            build_ids = flatten(build_by_branch_ids.values())
            build_dict = {build.id: build for build in build_obj.browse(
                cr, uid, build_ids, context=request.context)}

            def branch_info(branch, branch_dependency, prebuild):
                '''
                Documentation TODO
                @params
                @param branch_dependency: TODO
                @param prebuild: TODO
                '''
                key = (
                    branch.id,
                    branch_dependency and branch_dependency.id or None,
                    prebuild and prebuild.id or None,
                )
                return {
                    'branch': branch,
                    'branch_dependency': branch_dependency,
                    'prebuild': prebuild,
                    'builds': [
                        self.build_info(build_dict[build_id]) for build_id
                            in build_by_branch_ids[key]
                            ]
                }

            res.qcontext.update({
                'branches': [branch_info(
                    branch_id and branch_obj.browse(cr, uid, [branch_id],
                        context=request.context)[0] or None,
                    branch_dependency_id and branch_obj.browse(cr, uid,
                        [branch_dependency_id],
                        context=request.context)[0] or None,
                    prebuild_id and branch_obj.browse(cr, uid, [prebuild_id],
                        context=request.context)[0] or None,
                    )
                    for branch_id, branch_dependency_id, prebuild_id in\
                        build_by_branch_ids],
                'testing': build_obj.search_count(cr, uid, [
                    ('team_id', '=', team.id), ('state', '=', 'testing')]),
                'team': team,
                'running': build_obj.search_count(cr, uid, [
                    ('team_id', '=', team.id), ('state', '=', 'running')]),
                'pending': build_obj.search_count(cr, uid, [
                    ('team_id', '=', team.id), ('state', '=', 'pending')]),
                'qu': QueryURL('/runbot/team/' + slug(team), search=search,
                    limit=limit, refresh=refresh, **filters),
                'filters': filters,
            })

        return res

    def build_info(self, build):
        '''
        Inherit function to load info from prebuild and build.
        @params
        @param build: Object of build to get information
        '''
        res = super(RunbotController, self).build_info(build)
        res.update({'prebuild_id': build.prebuild_id,
                    'build': build})
        return res

    @http.route(['/runbot/build/<build_id>'], type='http', auth="public",
        website=True)
    def build(self, build_id=None, search=None, **post):
        '''
        Documentation TODO
        @params
        @param build_id: TODO
        @param search: TODO
        @param post: TODO
        '''
        registry, cr, uid = request.registry, request.cr, SUPERUSER_ID
        res = super(RunbotController, self).build(
            build_id=build_id, search=search, **post)
        build_brw = registry['runbot.build'].browse(cr, uid, int(build_id))
        if build_brw.team_id.name:
            res.qcontext.update({'team': build_brw.team_id})
        return res

    @http.route(['/runbot/build/<build_id>/force'], type='http', auth="public",
        website=True)
    def build_force(self, build_id, **post):
        '''
        Documentation TODO
        @params
        @param build_id: TODO
        @param post: TODO
        '''
        registry, cr, uid = request.registry, request.cr, SUPERUSER_ID
        res = super(RunbotController, self).build_force(build_id, **post)
        build_brw = registry['runbot.build'].browse(cr, uid, int(build_id))
        if build_brw.team_id.name:
            return werkzeug.utils.redirect(\
                '/runbot/team/%s' % build_brw.team_id.id)
        else:
            return res

    @http.route(['/runbot/build/<build_id>/label/<label_id>'], type='http',
        auth="public", method='POST')
    def toggle_label(self, build_id=None, label_id=None, search=None, **post):
        '''
        Documentation TODO
        @params
        @param build_id: TODO
        @param label_id: TODO
        @param search: TODO
        @param post: TODO
        '''
        registry, cr, uid = request.registry, request.cr, SUPERUSER_ID

        build_brw = registry['runbot.build'].browse(
            cr, uid, [int(build_id)])[0]
        res = super(RunbotController, self).toggle_label(
            build_id=build_id, label_id=label_id, search=search, **post)
        if build_brw.team_id.name:
            return werkzeug.utils.redirect(\
                '/runbot/team/%s' % build_brw.team_id.id)
        else:
            return res
