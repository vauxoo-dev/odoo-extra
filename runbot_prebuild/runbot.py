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
    This file inherit the models and methods that are needs to can create
    runbots prebuild and with this create builds.
"""
from openerp.osv import fields, osv
import os
import shutil
import logging
import glob

import datetime
import dateutil.parser

from openerp.addons.runbot.runbot import mkdirs, decode_utf, run
from openerp.addons.runbot.runbot import RunbotController
from openerp import http
from openerp.http import request
import werkzeug
import urllib

_logger = logging.getLogger(__name__)

REFS_FETCH_DEFAULT = [
    '+refs/heads/*:refs/heads/*', '+refs/pull/*/head:refs/pull/*']
REFS_GET_DATA = ['refs/heads', 'refs/pull']

class runbot_branch(osv.osv):

    '''
    The inherit of this class is used to add the field full_name in the model
    '''
    _inherit = "runbot.branch"

    def name_get(self, cr, uid, ids, context=None):
        '''
        This method is used to in the fields many2one to this model show
        the format: [branch.name] branch.repo_id.name
        @params
        @param cr: A database cursor
        @param uid: ID of the user currently logged in
        @param ids: list of ids for which name should be read
        @param context: context arguments, like lang, time zone
        '''
        if isinstance(ids, (list, tuple)) and not len(ids):
            return []
        if isinstance(ids, (long, int)):
            ids = [ids]
        res_name = super(runbot_branch, self).name_get(
            cr, uid, ids, context=context)
        res = []
        for record in res_name:
            branch = self.browse(cr, uid, [record[0]], context=context)[0]
            name = '[' + record[1] + '] ' + branch.repo_id.name
            res.append((record[0], name))
        return res

    def name_search(self, cr, uid, name, args=None, operator='ilike',
                    context=None, limit=100):
        '''
        This method added in the many2one to this model that can search by
        full_name.
        @params
        @param cr: A database cursor
        @param uid: ID of the user currently logged in
        @param name: value to search
        @param args: Other elements to consider in the search
        @param operator: Operator used in the search
        @param context: context arguments, like lang, time zone
        @param limit: Limit of registers to return in the search 
        '''
        if not args:
            args = []
        if not context:
            context = {}
        ids = []
        res = super(runbot_branch, self).name_search(cr, uid, name, args=args,
            operator=operator, context=context, limit=limit)
        for element in res:
            ids.append(element[0])
        if name:
            ids2 = self.search(cr, uid, [('full_name', operator, name)] + args,
                               limit=limit, context=context)
            ids.extend(ids2)
        ids = list(set(ids))
        return self.name_get(cr, uid, ids, context)

    def _get_branch_fullname(self, cr, uid, ids, name, args, context=None):
        '''
        This method is used to load data in full_name field with the next
        format:
        [branch.name] branch.repo_id.name
        @params
        @param cr: A database cursor
        @param uid: ID of the user currently logged in
        @param ids: list of ids for which name should be read
        @param name: TODO
        @param args: TODO
        @param context: context arguments, like lang, time zone
        '''
        if context is None:
            context = {}
        res = {}
        for branch in self.browse(cr, uid, ids, context=context):
            res[branch.id] = '[' + (branch.name or '') + '] ' + (
                branch.repo_id and branch.repo_id.name or '')
        return res

    def _get_name_repo(self, cr, uid, repo_ids, context=None):
        '''
        This method is used to update data in full_name field with the next
        format when the name of repo is changed:
            [branch.name] branch.repo_id.name
        @params
        @param cr: A database cursor
        @param uid: ID of the user currently logged in
        @param repo_id: TODO
        @param context: context arguments, like lang, time zone
        '''
        if context is None:
            context = {}
        branch_obj = self.pool.get('runbot.branch')
        branch_ids = branch_obj.search(cr, uid, [
            ('repo_id', 'in', repo_ids), ], context=context)
        return branch_ids

    _columns = {
        'full_name': fields.function(_get_branch_fullname, string='Full name',
            type='char',
            store={
                'runbot.branch': (lambda self, cr, uid, ids, c={}: ids,
                    ['name'], 50),
                'runbot.repo': (_get_name_repo, ['name', ], 50),
            }),
        'last_change_date': fields.datetime('Last change'),
        'last_sha': fields.char('Last sha'),
    }


class runbot_build(osv.osv):
    '''
    DOCUMENTATION TODO
    '''
    _inherit = "runbot.build"

    def fetch_build_lines(self, cr, uid, ids, context=None):
        '''
        Documentation TODO
        @params
        @param cr: A database cursor
        @param uid: ID of the user currently logged in
        @param ids: list of ids for which name should be read
        @param context: context arguments, like lang, time zone
        '''
        if isinstance(ids, (int, long)):
            ids = [ids]
        build_line_pool = self.pool.get('runbot.build.line')
        build_line_ids = build_line_pool.search(
            cr, uid, [('build_id', 'in', ids)], context=context)
        build_line_pool.fetch_build_line(
            cr, uid, build_line_ids, context=context)
        return build_line_ids

    def copy(self, cr, uid, id, default=None, context=None):
        '''
        Documentation TODO
        @params
        @param cr: A database cursor
        @param uid: ID of the user currently logged in
        @param id: list of ids for which name should be read
        @param default: Dict of values to assign in the copy
        @param context: context arguments, like lang, time zone
        '''
        if context is None:
            context = {}
        fetch_build = context.get('fetch_build', True)
        new_id = super(runbot_build, self).copy(
            cr, uid, id, default, context=context)
        if fetch_build:
            self.fetch_build_lines(cr, uid, [new_id], context=context)
        return new_id

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
        'from_main_prebuild_ok': fields.boolean('', copy=True,
            help="This build was created by a main prebuild?"
            "\nTrue: Then you will show at start on qweb"),
        'prebuild_id': fields.many2one('runbot.prebuild', ondelete='cascade',
            string='Runbot Pre-Build', required=False,
            help="This is the origin of instance data.", copy=True),
        'line_ids': fields.one2many('runbot.build.line', 'build_id',
            string='Build branches lines', readonly=False, copy=True),
        'team_id': fields.many2one('runbot.team', 'Team', help='Team of work',
            copy=True),
        'change_prebuild_ok': fields.boolean('Change prebuild?',
            help="True: If change prebuild after of created this build"),
        'name_id': fields.function(_get_url_name_id,
                                   string='URL Name ID',
                                   type='char',
                                   help='Contains the id for create href'),
    }

    _defaults = {
        'change_prebuild_ok': False,
    }

    def force_schedule(self, cr, uid, ids, context=None):
        '''
        Method used to call the function scheduler from runbot.repo
        @params
        @param cr: A database cursor
        @param uid: ID of the user currently logged in
        @param ids: list of ids for which name should be read
        @param context: context arguments, like lang, time zone
        '''
        if context is None:
            context = {}
        context.update({'build_ids': ids})
        build_obj = self.pool.get('runbot.repo')
        return build_obj.scheduler(cr, uid, ids=None, context=context)

    def checkout_prebuild(self, cr, uid, ids, context=None):
        '''
        Documentation TODO
        @params
        @param cr: A database cursor
        @param uid: ID of the user currently logged in
        @param ids: list of ids for which name should be read
        @param context: context arguments, like lang, time zone
        '''
        if context is None:
            context = {}
        for build in self.browse(cr, uid, ids, context=context):
            if not build.line_ids:
                build.skip()
            # starts from scratch
            if os.path.isdir(build.path()):
                shutil.rmtree(build.path())
            _logger.debug('Creating build in path "%s"' % (build.path()))

            # runbot log path
            mkdirs([build.path("logs"), build.server('addons')])

            # v6 rename bin -> openerp
            if os.path.isdir(build.path('bin/addons')):
                shutil.move(build.path('bin'), build.server())
            for build_line in build.line_ids:
                if build_line.repo_id.type == 'main':
                    path = build.path()
                elif build_line.repo_id.type == 'module':
                    path = build.server("addons")
                else:
                    pass  # TODO: raise error
                build_line.repo_id.git_export(
                    build_line.sha or build_line.branch_id.name, path)
            # move all addons to server addons path
            for module in glob.glob(build.path('addons/*')):
                module_new_path = os.path.join( build.server('addons'), os.path.basename( module ) )
                if os.path.isdir( module_new_path ):
                    shutil.rmtree( module_new_path )
                    _logger.debug('Deleting exists module "%s". Overwrite from native module' % ( module_new_path ))

                shutil.move(module, build.server('addons'))

    def checkout(self, cr, uid, ids, context=None):
        '''
        Documentation TODO
        @params
        @param cr: A database cursor
        @param uid: ID of the user currently logged in
        @param ids: list of ids for which name should be read
        @param context: context arguments, like lang, time zone
        '''
        for build in self.browse(cr, uid, ids, context=context):
            if not build.prebuild_id:
                return super(runbot_build, self).checkout(cr, uid, ids,
                    context=context)
            else:
                self.checkout_prebuild(cr, uid, [build.id], context=context)

    def unlink(self, cr, uid, ids, context=None):
        '''Inherit super method unlink to kill build before of delete it
        @params
        @param cr: A database cursor
        @param uid: ID of the user currently logged in
        @param ids: list of ids for which name should be read
        @param context: context arguments, like lang, time zone
        '''
        self.kill(cr, uid, ids, context=context)
        return super(runbot_build, self).unlink(cr, uid, ids, context=context)

    def write(self, cr, uid, ids, vals, context=None):
        '''Inherit super method write to kill build when change from
        state='running' to state='done'
        @params
        @param cr: A database cursor
        @param uid: ID of the user currently logged in
        @param ids: list of ids for which name should be read
        @param context: context arguments, like lang, time zone
        '''
        if context is None:
            context = {}
        terminate_ok = context.get('terminate_ok', True)
        if terminate_ok:
            if 'state' in vals:
                if vals['state'] == 'done':
                    #Maybe this value was already assigned with state equal to "good"
                    build_ids_to_kill = self.search(cr, uid, [
                        ('state', 'not in', ['done', 'pending']),
                        ('id', 'in', ids),
                    ], context=context)
                    #Not self.kill because this one rewrite result='killed'
                    # and it need original state.
                    context2 = context.copy()
                    context2.update({'terminate_ok': False})
                    self.kill(cr, uid, build_ids_to_kill, context=context2)
        res = super(runbot_build, self).write(cr, uid, ids, vals,\
                                               context=context)
        return res

    def get_repo_branch_name(self, cr, uid, ids, context=None):
        """
        This method inherit to get all repo id and branch name
            from a build.
        Include new build line data from prebuild.
        return dict {repo.id = branch_name}
        """
        if not ids:
            return True
        if isinstance(ids, (int, long)):
            ids = [ids]
        repo_branch_data = {}
        for build in self.browse(cr, uid, ids, context=context):
            if build.prebuild_id:
                for build_line in build.line_ids:
                    if build_line.repo_id.check_pylint:
                        repo_branch_data[build_line.repo_id.id] =\
                            build_line.sha
            else:
                repo_branch_data.update(
                    super(runbot_build, self).get_repo_branch_name(
                        cr, uid, [build.id], context=context
                    )
                )
        return repo_branch_data

    def github_status_prebuild(self, cr, uid, ids, context=None):
        runbot_domain = self.pool['runbot.repo'].domain(cr, uid)
        for build in self.browse(cr, uid, ids, context=context):
            if build.change_prebuild_ok:
                continue
            for build_line in build.line_ids:
                if build_line.reason_pr_ok \
                   and build_line.create_status_ok:
                    if build_line.branch_id.repo_id.host_driver == 'github' and build_line.branch_id.repo_id.token:
                        desc = "runbot build %s - from prebuild %s" % (build.dest, build.prebuild_id.name)
                        if build.state == 'testing':
                            state = 'pending'
                        elif build.state in ('running', 'done'):
                            state = 'error'
                            if build.result == 'ok':
                                state = 'success'
                            if build.result == 'ko':
                                state = 'failure'
                            desc += " (runtime %ss)" % (build.job_time,)
                        else:
                            continue
                        status = {
                            "state": state,
                            "target_url": "http://%s/runbot/build/%s" % (runbot_domain, build.id),
                            "description": desc,
                            "context": "ci/runbot%s" % (build.prebuild_id.id),
                        }
                        _logger.debug("github updating status %s to %s", build.name, state)
                        build_line.branch_id.repo_id.github('/repos/:owner/:repo/statuses/%s' % build_line.sha, status)
                    else:
                        _logger.debug("github NO updating status. No token or not github repo [%s]" % (build_line.branch_id.repo_id.name) )
        return True


    def github_status(self, cr, uid, ids, context=None):
        build_from_prebuild_ids =  [build.id for build in self.browse(cr, uid, ids, context=context) if build.prebuild_id]
        build_not_prebuild_ids = []
        for build_id in ids:
            if build_id not in build_from_prebuild_ids:
                build_not_prebuild_ids.append(build_id)
        self.github_status_prebuild(cr, uid, build_from_prebuild_ids, context=context)
        return super(runbot_build, self).github_status(cr, uid, build_not_prebuild_ids, context=context)


class runbot_repo(osv.osv):
    '''
    This class add the field team to assign to repo.
    '''
    _inherit = "runbot.repo"

    _columns = {
        'team_id': fields.many2one('runbot.team', 'Team', help='Team of work',
            copy=True),
    }

    def create_branches(self, cr, uid, ids, ref=REFS_GET_DATA, context=None):
        '''
        Documentation TODO
        @params
        @param cr: A database cursor
        @param uid: ID of the user currently logged in
        @param ids: list of ids for which name should be read
        @param ref: TODO
        @param context: context arguments, like lang, time zone
        '''
        branch_pool = self.pool.get('runbot.branch')
        branch_ids = []
        repo_id_ref_dict = self.get_ref_data(cr, uid, ids, ref=ref,
            fields=['refname', 'objectname', 'committerdate:iso8601'], context=context)
        # days to check branches
        icp = self.pool['ir.config_parameter']
        days_to_check = int(icp.get_param(cr, uid, 'runbot.days_to_check', default=30))
        for repo_id in repo_id_ref_dict.keys():
            for refs in repo_id_ref_dict[repo_id]:
                if dateutil.parser.parse(refs['committerdate:iso8601'][:19]) + datetime.timedelta(days_to_check) < datetime.datetime.now():
                    _logger.debug(
                        "skip 'create_branches' for old branches"
                        " Ordered by date then first old branch found is a break"
                    )
                    break

                name = refs.get('refname') or False
                if name:
                    current_branch_ids = branch_pool.search(cr, uid,
                        [('repo_id', '=', repo_id), ('name', '=', name)], limit=1)
                    current_branch_id = current_branch_ids and current_branch_ids[0] or False
                    if not current_branch_id:
                        _logger.debug(
                            'repo id %s found new branch %s', repo_id, name)
                        try:
                            current_branch_id = branch_pool.create(
                                cr, uid, {'repo_id': repo_id, 'name': name})
                            branch_ids.append(current_branch_id)
                        except:
                            # cron is executed for a ir.cron or button.
                            # This make create from different cursor.
                            # This make a error of unique branch name in same
                            # repo_id
                            pass
                    # validate if branch was created or found to update last date and sha
                    if current_branch_id:
                        branch_pool.write(
                            cr, uid, [current_branch_id],
                            {'last_sha': refs.get('objectname'), 'last_change_date': refs.get('committerdate:iso8601')},
                            context=context)
        return branch_ids

    def git(self, cr, uid, ids, cmd, context=None):
        '''
        Documentation TODO
        @params
        @param cr: A database cursor
        @param uid: ID of the user currently logged in
        @param ids: list of ids for which name should be read
        @param cmd: TODO
        @param context: context arguments, like lang, time zone
        '''
        # make a log debug if path not exists
        for repo in self.browse(cr, uid, ids, context=context):
            if os.path.exists(repo.path):
                return super(runbot_repo, self).git(cr, uid, ids, cmd=cmd,
                    context=context)
        _logger.debug('repo path %s not found', repo.path)

    def get_ref_data(self, cr, uid, ids, ref, fields=None, rename_fields=None,
        context=None):
        '''
        Documentation TODO
        @params
        @param cr: A database cursor
        @param uid: ID of the user currently logged in
        @param ids: list of ids for which name should be read
        @param ref: TODO
        @param fields: TODO
        @param rename_fields: TODO
        @param context: context arguments, like lang, time zone
        '''
        if fields is None:
            # TODO: Set var global. And get dict of new localnames
            fields = ['refname', 'objectname', 'committerdate:iso8601',
                      'authorname', 'subject', 'committername']
        if rename_fields is None:
            rename_fields = fields
        if 'committerdate:iso8601' not in fields:
            fields.append('committerdate:iso8601')
        if isinstance(ref, str) or isinstance(ref, basestring):
            ref = ref.split(',')
        res = {}
        for repo in self.browse(cr, uid, ids, context=context):
            res[repo.id] = []
            fmt = "%00".join(["%(" + field + ")" for field in fields])
            cmd = ['for-each-ref', '--format', fmt, '--sort=-committerdate']
            cmd.extend(ref)
            git_refs = repo.git(cmd)
            if git_refs:
                git_refs = git_refs.strip()
                refs = [[decode_utf(field) for field in line.split(
                    '\x00')] for line in git_refs.split('\n')]
                for data_field in refs:
                    res[repo.id].append(dict(zip(rename_fields, data_field)))
        return res

    def fetch_git(self, cr, uid, ids, refs=REFS_FETCH_DEFAULT, context=None):
        '''
        Documentation TODO
        @params
        @param cr: A database cursor
        @param uid: ID of the user currently logged in
        @param ids: list of ids for which name should be read
        @param refs: TODO
        @param context: context arguments, like lang, time zone
        '''
        if context is None:
            context = {}
        clone_only = context.get('clone_only', False)
        repo_updated_ids = []
        for repo in self.browse(cr, uid, ids, context=context):
            _logger.debug('repo %s fetch branches', repo.name)
            if not os.path.isdir(os.path.join(repo.path)):
                os.makedirs(repo.path)
            if not os.path.isdir(os.path.join(repo.path, 'refs')):
                try:
                    run(['git', 'clone', '--bare', repo.name, repo.path])
                    repo_updated_ids.append(repo.id)
                except:
                    # TODO: Get exception of lost connection... no internet
                    pass
            else:
                if not clone_only:
                    for ref in refs:
                        try:
                            repo.git(['fetch', '-p', 'origin', ref])
                            repo_updated_ids.append(repo.id)
                        except:
                            # TODO: Get exception of lost connection... no
                            # internet
                            pass
        return repo_updated_ids

    def get_sticky_repo_ids(self, cr, uid, ids, context=None):
        '''
        Search sticky repo from prebuild sticky
        @params
        @param cr: A database cursor
        @param uid: ID of the user currently logged in
        @param ids: list of ids for which name should be read
        @param ref: TODO
        @param context: context arguments, like lang, time zone
        '''
        if context is None:
            context = {}
        prebuild_pool = self.pool.get('runbot.prebuild')
        prebuild_line_pool = self.pool.get('runbot.prebuild.branch')

        prebuild_sticky_ids = prebuild_pool.search(
            cr, uid, [('sticky', '=', True)], context=context)

        # Search repo used into prebuild from sticky build (and check pr or
        # check new commit) to update
        prebuild_line_sticky_ids = prebuild_line_pool.search(cr, uid, [
            ('prebuild_id', 'in', prebuild_sticky_ids),
        ], context=context)
        prebuild_line_datas = prebuild_line_pool.read(
            cr, uid, prebuild_line_sticky_ids, ['repo_id'], context=context)
        repo_ids = list(set([prebuild_line_data['repo_id'][0]
                             for prebuild_line_data in prebuild_line_datas]))
        return repo_ids

    def update_by_ids(self, cr, uid, ids, context=None):
        if context is None:
            context = {}
        # Clone first time
        context2 = context.copy()
        context2.update({'clone_only': True})
        self.fetch_git(
            cr, uid, ids, context=context2)

        # Fetch repo
        self.fetch_git(
            cr, uid, ids, context=context)
        new_branch_ids = self.create_branches(
            cr, uid, ids, context=context)
        return ids

    def update(self, cr, uid, ids=None, context=None):
        '''
        Documentation TODO
        @params
        @param cr: A database cursor
        @param uid: ID of the user currently logged in
        @param ids: list of ids for which name should be read
        @param context: context arguments, like lang, time zone
        '''
        # All active repo get last version and new branches
        if context is None:
            context = {}
        if ids is None:
            ids = []
        # Clone first time of all branches
        context2 = context.copy()
        context2.update({'clone_only': True})
        all_repo_ids = self.pool.get('runbot.repo').search(
            cr, uid, [], context=context)
        repo_cloned_ids = self.fetch_git(
            cr, uid, all_repo_ids, context=context2)

        # Fetch sticky repo
        repo_sticky_ids = self.get_sticky_repo_ids(
            cr, uid, ids, context=context)
        repo_fetched_ids = self.fetch_git(
            cr, uid, repo_sticky_ids, context=context)

        # Create new branches from previous fetch and clone
        repo_ids = list(set(repo_cloned_ids + repo_fetched_ids))
        new_branch_ids = self.create_branches(
            cr, uid, repo_ids, context=context)

        # Create build from prebuild configuration
        self.create_build_from_prebuild(cr, uid, None, context=context)

        # Continue with normal process
        # but before fix a error. If path not exists then no update.
        ids = list(set(ids))
        for repo_data in self.read(cr, uid, ids, ['path'], context=context):
            if not os.path.isdir(os.path.join(repo_data['path'], 'refs')):
                ids.pop(ids.index(repo_data['id']))

        return super(runbot_repo, self).update(cr, uid, ids, context=context)

    def create_build_from_prebuild(self, cr, uid, ids=None, context=None):
        '''
        Method used to create a runbot build record based in the runbot
        prebuild
        @params
        @param cr: A database cursor
        @param uid: ID of the user currently logged in
        @param ids: list of ids for which name should be read
        @param ref: TODO
        @param context: context arguments, like lang, time zone
        '''
        if context is None:
            context = {}
        prebuild_pool = self.pool.get('runbot.prebuild')
        build_pool = self.pool.get('runbot.build')
        prebuild_line_pool = self.pool.get('runbot.prebuild.branch')

        prebuild_sticky_ids = prebuild_pool.search(
            cr, uid, [('sticky', '=', True)], context=context)

        # Search repo used into prebuild from sticky build (and check pr or
        # check new commit) to update
        prebuild_line_sticky_ids = prebuild_line_pool.search(cr, uid, [
            '&', ('prebuild_id', 'in', prebuild_sticky_ids),
            '|', ('check_pr', '=', True),
            ('check_new_commit', '=', True),
        ], context=context)
        prebuild_line_datas = prebuild_line_pool.read(
            cr, uid, prebuild_line_sticky_ids, ['repo_id'], context=context)
        repo_ids = list(set([prebuild_line_data['repo_id'][0]
                             for prebuild_line_data in prebuild_line_datas]))

        # fetch repo
        self.fetch_git(cr, uid, repo_ids, context=context)

        # create build from prebuild of new commit
        prebuild_pool.create_prebuild_new_commit(
            cr, uid, prebuild_sticky_ids, context=context)

        # create build from prebuild of pr
        prebuild_pool.create_build_pr(
            cr, uid, prebuild_sticky_ids, context=context)

        # Get build_ids with prebuild_id set it. And assign in context for use
        # it in scheduler function
        builds_from_prebuild_ids = build_pool.search(
            cr, uid, [('prebuild_id', '<>', False)], context=context)
        context['build_ids'] = builds_from_prebuild_ids
        return builds_from_prebuild_ids

    def get_branch_repo(self, cr, uid, ids, context=None):
        '''
        Method to get the branches that have assigned the repo
        @params
        @param cr: A database cursor
        @param uid: ID of the user currently logged in
        @param ids: list of ids for which name should be read
        @param ref: TODO
        @param context: context arguments, like lang, time zone
        '''
        if context is None:
            context = {}
        branch_obj = self.pool.get('runbot.branch')
        branch_ids = branch_obj.search(
            cr, uid, [('repo_id', 'in', ids)], context=context)
        return {
            'name': 'Branch Repo',
            'res_model': 'runbot.branch',
            'view_type': 'form',
            'view_mode': 'tree,form',
            'type': 'ir.actions.act_window',
            'domain': [('id', 'in', branch_ids)],
        }

    def get_prebuild_repo(self, cr, uid, ids, context=None):
        '''
        Method to get the runbot prebuilds that have assigned the repo in
        yours lines
        @params
        @param cr: A database cursor
        @param uid: ID of the user currently logged in
        @param ids: list of ids for which name should be read
        @param context: context arguments, like lang, time zone
        '''
        if context is None:
            context = {}
        branch_obj = self.pool.get('runbot.branch')
        prebuild_bra_obj = self.pool.get('runbot.prebuild.branch')
        branch_ids = branch_obj.search(
            cr, uid, [('repo_id', 'in', ids)], context=context)
        pre_bra_ids = prebuild_bra_obj.search(
            cr, uid, [('branch_id', 'in', branch_ids)])
        prebuild_ids = []
        for pre_bra in prebuild_bra_obj.browse(cr, uid, pre_bra_ids,
            context=context):
            if pre_bra.prebuild_id and pre_bra.prebuild_id.id:
                prebuild_ids.append(pre_bra.prebuild_id.id)
        prebuild_ids = list(set(prebuild_ids))
        return {
            'name': 'Prebuild Repo',
            'res_model': 'runbot.prebuild',
            'view_type': 'form',
            'view_mode': 'tree,form',
            'type': 'ir.actions.act_window',
            'domain': [('id', 'in', prebuild_ids)],
        }


class RunbotController(RunbotController):

    @http.route(['/runbot/build/<build_id>/kill'], type='http',
                auth="public", website=True)
    def build_kill(self, build_id, **post):
        registry = request.registry
        cr = request.cr
        uid = 1
        context = request.context
        registry['runbot.build'].kill(cr, uid, [int(build_id)])
        build = registry['runbot.build'].browse(cr, uid, [int(build_id)])[0]
        return werkzeug.utils.redirect('/runbot/repo/%s' % build.repo_id.id)
