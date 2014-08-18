# -*- encoding: utf-8 -*-
#TODO: license from vauxoo. PEP8. pylint
from openerp.osv import fields, osv
import os
import shutil
import logging
import glob
import time
import werkzeug
from collections import OrderedDict

from openerp.addons.runbot.runbot import RunbotController
import dateutil.parser
from openerp.addons.runbot.runbot import uniq_list
from openerp.addons.runbot.runbot import flatten
from openerp.http import request
from openerp import http
from openerp.addons.website.models.website import slug
from openerp.addons.website_sale.controllers.main import QueryURL
from openerp import SUPERUSER_ID
from openerp import tools

_logger = logging.getLogger(__name__)

def mkdirs(dirs):#Copy original function from addons/runbot/runbot.py. TODO: import function
    for d in dirs:
        if not os.path.exists(d):
            os.makedirs(d)

def decode_utf(field):#Copy original function from addons/runbot/runbot.py TODO: import function
    try:
        return field.decode('utf-8')
    except UnicodeDecodeError:
        return ''
def log(*l, **kw):#Copy original function from addons/runbot/runbot.py. TODO: import function
    out = [i if isinstance(i, basestring) else repr(i) for i in l] + \
          ["%s=%r" % (k, v) for k, v in kw.items()]
    _logger.debug(' '.join(out))

def run(l, env=None):#Copy original function from addons/runbot/runbot.py TODO: import function
    """Run a command described by l in environment env"""
    log("run", l)
    env = dict(os.environ, **env) if env else None
    if isinstance(l, list):
        if env:
            rc = os.spawnvpe(os.P_WAIT, l[0], l, env)
        else:
            rc = os.spawnvp(os.P_WAIT, l[0], l)
    elif isinstance(l, str):
        tmp = ['sh', '-c', l]
        if env:
            rc = os.spawnvpe(os.P_WAIT, tmp[0], tmp, env)
        else:
            rc = os.spawnvp(os.P_WAIT, tmp[0], tmp)
    log("run", rc=rc)
    return rc

REFS_FETCH_DEFAULT = ['+refs/heads/*:refs/heads/*', '+refs/pull/*/head:refs/pull/*']
REFS_GET_DATA = ['refs/heads', 'refs/pull']

class runbot_prebuild_branch(osv.osv):
    _name = "runbot.prebuild.branch"
    _rec_name = 'branch_id'

    _columns = {
        'branch_id': fields.many2one('runbot.branch', 'Branch', required=True,
            ondelete='cascade', select=1),
        'check_pr': fields.boolean('Check PR',
            help='If is True, this will check Pull Request for this branch in this prebuild', copy=False),
        'check_new_commit': fields.boolean('Check New Commit',
            help='If is True, this will check new commit for this branch in this prebuild'\
            ' and will make a new build.', copy=False),
        'sha': fields.char('SHA commit', size=40,
            help='Empty=Currently version\nSHA=Get this version in builds'),
        'prebuild_id': fields.many2one('runbot.prebuild', 'Pre-Build', required=True,
            ondelete='cascade', select=1),
        'repo_id': fields.related('branch_id', 'repo_id', type="many2one",
            relation="runbot.repo", string="Repository", readonly=True, store=True,
            ondelete='cascade', select=1),
    }


class runbot_team(osv.Model):
    '''
    Object used to create team to work in runbot
    '''

    _name = 'runbot.team'

    _columns = {
        'name': fields.char('Name', help='Name of the team'),
        'description': fields.text('Desciption', help='A little description of the team'),
        'privacy_visibility': fields.selection(
            [('public', 'Public'), ('private', 'Private')], 'Privacy Visibility'),
    }

class runbot_prebuild(osv.osv):
    _name = "runbot.prebuild"

    _columns = {
        'name': fields.char("Name", size=128, required=True),
        'team_id': fields.many2one('runbot.team', 'Team', help='Team of work', copy=True, required=True),
        'module_branch_ids': fields.one2many('runbot.prebuild.branch', 'prebuild_id',
            string='Branches of modules', copy=True,
            help="Community addons branches which need to run tests."),
        'sticky': fields.boolean('Sticky', select=1,
            help="If True: Stay alive a instance ever. And check PR to main branch and"\
            " modules branches for make pre-builds\nIf False: Stay alive a instance only"\
            " moment and not check PR.", copy=False),
        'modules': fields.char("Modules to Install", size=256,
            help="Empty is all modules availables", copy=True),
        'lang': fields.selection(tools.scan_languages(), 'Language', help='Language to change '
                                 'instance after of run test.', copy=True),
        'pylint_config': fields.many2one('pylint.conf', string='Pylint Config'),
        'modules_to_exclude': fields.char("Modules to exclude", size=256,
            help="Empty is exclude none. Add modules is exclude this one. FEATURE TODO", copy=True),
        'script_prebuild': fields.text('Script Pre-Build',
            help="Script to execute before run build", copy=True),
        'script_posbuild': fields.text('Script Pos-Build',
            help="Script to execute after run build", copy=True),
        'prebuild_parent_id': fields.many2one('runbot.prebuild', 'Parent Prebuild', copy=True,
            help="If this is a prebuild from PR this field is for set original prebuild"),
    }

    _defaults = {
        'sticky': False,
    }
    #TODO: Add constraint that add prebuild_lines of least one main repo type
    #TODO: Add related to repo.type store=True
    
    def get_builds_prebuild(self, cr, uid, ids, context=None):
        '''
        Method to get the builds that have been generated with this prebuild
        '''
        if context is None:
            context = {}
        build_obj = self.pool.get('runbot.build')
        build_ids = build_obj.search(cr, uid, [('prebuild_id', 'in', ids)], context=context)
        return {
            'name': 'Prebuild Origin',
            'res_model': 'runbot.build',
            'view_type': 'form',
            'view_mode': 'tree,form',
            'type': 'ir.actions.act_window',
            'domain': [('id', 'in', build_ids)],
        }

    def create_prebuild_new_commit(self, cr, uid, ids, context=None):
        """
        Create new build with changes in your branches with check_new_commit=True
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
            ], context=context)
            if not build_ids:
                #If not build exists then create it and mark as from_main_prebuild_ok=True
                build_new_id = self.create_build(cr, uid, [prebuild_id],
                    default_data={'from_main_prebuild_ok': True}, context=context)
                build_new_ids.append( build_new_id )
                continue

            build_line_ids = build_line_pool.search(cr, uid, [
                ('build_id', 'in', build_ids),
                ('prebuild_line_id.check_new_commit', '=', True),
            ], context=context)
            if build_line_ids:
                #Get all branches from build_line of this prebuild_sticky
                build_line_datas = build_line_pool.read(cr, uid, build_line_ids, ['branch_id'], context=context)
                branch_ids = list( set( [ r['branch_id'][0] for r in build_line_datas ] ) )
                #Get last commit and search it as sha of build line
                for branch in branch_pool.browse(cr, uid, branch_ids, context=context):
                    _logger.info("get last commit info for check new commit")
                    refs = repo_pool.get_ref_data(cr, uid, [branch.repo_id.id], branch.name, fields=['objectname'], context=context)
                    if refs and refs[branch.repo_id.id]:
                        ref_data = refs[branch.repo_id.id][0]
                        sha = ref_data['objectname']
                        build_line_with_sha_ids = build_line_pool.search(cr, uid, [
                            ('branch_id', '=', branch.id),('build_id', 'in', build_ids),
                            ('sha', '=', sha)], context=context, limit=1)
                        if not build_line_with_sha_ids:
                            #If not last commit then create build with last commit
                            replace_branch_info = {branch.id: {'reason_ok': True }}
                            default_data = {'from_main_prebuild_ok': True}
                            build_new_id = self.create_build(cr, uid, [prebuild_id], default_data=default_data, replace_branch_info=replace_branch_info, context=context)
                            build_new_ids.append( build_new_id )
        return build_new_ids

    def create_build_pr(self, cr, uid, ids, context=None):
        """
        Create build from pull request with build line check_pr=True
        """
        new_build_ids = [ ]
        branch_pool = self.pool.get('runbot.branch')
        build_pool = self.pool.get('runbot.build')
        build_line_pool = self.pool.get('runbot.build.line')
        for prebuild in self.browse(cr, uid, ids, context=context):
            for prebuild_line in prebuild.module_branch_ids:
                if prebuild_line.check_pr:
                    branch_pr_ids = branch_pool.search(cr, uid, [('branch_base_id', '=', prebuild_line.branch_id.id)], context=context)
                    #Get build of this prebuild
                    for branch_pr in branch_pool.browse(cr, uid, branch_pr_ids, context=context):
                        #prebuild_pr_ids = prebuild_line_pool.search(cr, uid, [('branch_id', '=', branch_pr.id), ('prebuild_id', 'in', prebuild_child_ids)], limit=1)
                        build_line_pr_ids = build_line_pool.search(cr, uid, [
                            ('branch_id', '=', branch_pr.id),
                            ('build_id.prebuild_id', '=', prebuild.id)], context=context)
                        if build_line_pr_ids:
                            #if exist build of pr no create new one
                            continue

                        #If not exist build of this pr then create one
                        replace_branch_info = {prebuild_line.branch_id.id: {
                            'branch_id': branch_pr.id,
                            'reason_ok': True,
                        }}
                        new_name = prebuild.name + ' [' + branch_pr.complete_name + ']'
                        build_created_ids = self.create_build(cr, uid, [prebuild.id],
                            default_data = {
                                'branch_id': branch_pr.id,#Only for group by in qweb view
                                'name': new_name,
                                'author': new_name,#TODO: Get this value.
                                'subject': new_name,#TODO: Get this value
                            }, replace_branch_info=replace_branch_info, context=context)
                        new_build_ids.extend( build_created_ids )
        return new_build_ids

    def create_main_build(self, cr, uid, ids, context=None):
        """
        Use it for send default data when use button directly
        """
        default_data = {
            'from_main_prebuild_ok': True,
        }
        return self.create_build(cr, uid, ids, default_data=default_data, context=context)

    def create_build(self, cr, uid, ids, default_data=None, replace_branch_info=None, context=None):
        """
        Create a new build from a prebuild.
        @replace_branch_info: Get a dict data for replace a old branch for new one.
            {branch_old_id: {'branch_id': integer, 'reason_ok': boolean}}#build_line_data
        """
        if context is None:
            context = {}
        if replace_branch_info is None:
            replace_branch_info = {}
        if default_data is None:
            default_data = {}
        branch_reason_ids = context.get('branch_reason_ids', []) or []
        repo_obj = self.pool.get('runbot.repo')
        build_obj = self.pool.get('runbot.build')
        branch_obj = self.pool.get('runbot.branch')
        build_ids = []
        for prebuild in self.browse(cr, uid, ids, context=context):
            #Update repository but no create default build
            #Get build_line current info
            build_line_datas = []
            for prebuild_line in prebuild.module_branch_ids:
                new_branch_info = replace_branch_info.get(prebuild_line.branch_id.id, {}) or {}
                branch_id = new_branch_info.get('branch_id', False) or prebuild_line.branch_id.id
                branch = branch_obj.browse(cr, uid, [branch_id], context=context)[0]

                _logger.info("get last commit info for create new build line")
                refs = repo_obj.get_ref_data(cr, uid, [branch.repo_id.id], branch.name, 
                        fields = ['refname', 'objectname', 'committerdate:iso8601', \
                            'authorname', 'subject', 'committername'],
                        rename_fields = ['refname', 'sha', 'date', 'author', 'subject', \
                            'committername'], context=context)
                if refs and refs[branch.repo_id.id]:
                    ref_data = refs[branch.repo_id.id][0]
                    ref_data.update( new_branch_info )
                    ref_data.update({
                        'branch_id': branch_id,
                        'prebuild_line_id': prebuild_line.id,
                    })
                    build_line_datas.append( (0, 0, ref_data) )

            build_info = {
                'branch_id': prebuild_line.branch_id.id,#Any branch. Useless. Last of for. TODO: Use a dummy branch for not affect normal process.
                'name': prebuild.name,
                'author': prebuild.name,#TODO: Get this value
                'subject': prebuild.name,#TODO: Get this value
                'date': time.strftime("%Y-%m-%d %H:%M:%S"),#TODO: Get this value
                'modules': prebuild.modules,
                'prebuild_id': prebuild.id,#Important field for custom build and custom checkout
                'team_id': prebuild and prebuild.team_id and prebuild.team_id.id or False,
                'line_ids': build_line_datas,
                'lang': prebuild.lang,
                'pylint_config': prebuild.pylint_config and prebuild.pylint_config.id or False,
            }
            build_info.update( default_data or {} )
            _logger.info("Create new build from prebuild_id [%s] "%(prebuild.name) )
            build_id = build_obj.create(cr, uid, build_info, context=context)
            build_ids.append( build_id )
        return build_ids

class runbot_branch(osv.osv):
    _inherit = "runbot.branch"

    def name_get(self, cr, uid, ids, context=None):
        if isinstance(ids, (list, tuple)) and not len(ids):
            return []
        if isinstance(ids, (long, int)):
            ids = [ids]
        res_name = super(runbot_branch, self).name_get(cr, uid, ids, context=context)
        res = []
        for record in res_name:
            branch = self.browse(cr, uid, [record[0]], context=context)[0]
            name = '[' + record[1] + '] ' + branch.repo_id.name
            res.append((record[0], name))
        return res

class runbot_build(osv.osv):
    _inherit = "runbot.build"

    _columns = {
        'from_main_prebuild_ok': fields.boolean('', copy=True,
            help="This build was created by a main prebuild?"\
               "\nTrue: Then you will show at start on qweb"),
        'prebuild_id': fields.many2one('runbot.prebuild', string='Runbot Pre-Build',
            required=False, help="This is the origin of instance data.", copy=True),
        'line_ids': fields.one2many('runbot.build.line', 'build_id',
            string='Build branches lines', readonly=True, copy=True),
        'team_id': fields.many2one('runbot.team', 'Team', help='Team of work', copy=True),
    }

    def force_schedule(self, cr, uid, ids, context=None):
        if context is None:
            context = {}
        context.update({'build_ids': ids})
        build_obj = self.pool.get('runbot.repo')
        return build_obj.scheduler(cr, uid, ids=None, context=context)

    def checkout_prebuild(self, cr, uid, ids, context=None):
        branch_obj = self.pool.get('runbot.branch')
        build_line_obj = self.pool.get('runbot.build.line')
        #main_branch = branch_obj.browse(cr, uid, [main_branch_id], context=context)[0]
        for build in self.browse(cr, uid, ids, context=context):
            if not build.line_ids:
                build.skip()
            # starts from scratch
            if os.path.isdir(build.path()):
                shutil.rmtree(build.path())
            _logger.debug('Creating build in path "%s"'%( build.path() ))

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
                    pass #TODO: raise error
                build_line.repo_id.git_export( build_line.sha or build_line.branch_id.name, path )
            # move all addons to server addons path
            for module in glob.glob( build.path('addons/*') ):
                shutil.move(module, build.server('addons'))

    def checkout(self, cr, uid, ids, context=None):
        for build in self.browse(cr, uid, ids, context=context):
            if not build.prebuild_id:
                return super(runbot_build, self).checkout(cr, uid, ids, context=context)
            else:
                self.checkout_prebuild(cr, uid, [build.id], context=context)

class runbot_build_line(osv.osv):
    _name = 'runbot.build.line'
    _rec_name = 'sha'

    def _get_url_commit(self, cr, uid, ids, fields, name, args, context=None):
        if not context:
            context = {}
        res = {}
        for line in self.browse(cr, uid, ids, context=context):
            repo = line.repo_id
            url = False
            if repo.host_driver == 'github':
                url = repo.url+'/commit/'+line.sha
            elif repo.host_driver == 'bitbucket':
                url = repo.url+'/commits/'+line.sha
            res[line.id] = url
        return res

    def _get_short_commit(self, cr, uid, ids, fields, name, args, context=None):
        if not context:
            context = {}
        res = {}
        for prebuild_line in self.browse(cr, uid, ids, context=context):
            res[ prebuild_line.id ] = len(prebuild_line.sha) > 7 and \
                prebuild_line.sha[:7] or prebuild_line.sha
        return res

    _columns = {
        'build_id': fields.many2one('runbot.build', 'Build', required=True,
            ondelete='cascade', select=1),
        'prebuild_line_id': fields.many2one('runbot.prebuild.branch', 'Prebuild Line',
            required=False, ondelete='set null', select=1),
        'branch_id': fields.many2one('runbot.branch', 'Branch', required=True,
            ondelete='cascade', select=1),
        'refname': fields.char('Ref Name'),
        'sha': fields.char('SHA commit', size=40,
            help='Version of commit or sha', required=True),
        'date': fields.datetime('Commit date'),
        'author': fields.char('Author'),
        'commit_url': fields.function(_get_url_commit, string='Commit URL', type='char', help='URL of last commit for this branch'),
        'subject': fields.text('Subject'),
        'committername': fields.char('Committer'),
        'repo_id': fields.related('branch_id', 'repo_id', type="many2one", \
            relation="runbot.repo", string="Repository", readonly=True, store=True, \
            ondelete='cascade', select=1),
        'reason_ok': fields.boolean('Reason', help="This line is the reason of create" \
             "the complete build.\nReason of PR or reason of new commit.", copy=False),
        'short_sha': fields.function(_get_short_commit, string='Short Commit', type='char',
            help='Sha short commit. Last 7 chars'),
    }

class runbot_repo(osv.osv):
    _inherit = "runbot.repo"
    
    _columns = {
        'team_id': fields.many2one('runbot.team', 'Team', help='Team of work', copy=True),
    }

    def create_branches(self, cr, uid, ids, ref=REFS_GET_DATA, context=None):
        branch_pool = self.pool.get('runbot.branch')
        branch_ids = []
        repo_id_ref_dict = self.get_ref_data(cr, uid, ids, ref=ref, fields=['refname', 'objectname'], context=context)
        for repo_id in repo_id_ref_dict.keys():
            for refs in repo_id_ref_dict[repo_id]:
                name = refs.get('refname') or False
                if name:
                    branch_ids = branch_pool.search(cr, uid, [('repo_id', '=', repo_id), ('name', '=', name)])
                    if not branch_ids:
                        _logger.debug('repo id %s found new branch %s', repo_id, name)
                        try:
                            branch_id = branch_pool.create(cr, uid, {'repo_id': repo_id, 'name': name})
                            branch_ids.append( branch_id )
                        except:
                            #cron is executed for a ir.cron or button. This make create from different cursor.
                            #This make a error of unique branch name in same repo_id
                            pass
        return branch_ids

    def get_ref_data(self, cr, uid, ids, ref, fields=None, rename_fields=None, context=None):
        if fields is None:
            #fields_dict = {'refname': 'refname', 'sha': 'objectname', 'date': 'committerdate:iso8601', 'author': 'authorname', 'subject', 'subject', 'committername': 'committername' }
            #fields = ['refname', 'objectname', 'committerdate:iso8601', 'authorname', 'subject', 'committername'], rename_fields = ['refname', 'sha', 'date', 'author', 'subject', 'committername']
            fields = ['refname', 'objectname', 'committerdate:iso8601', 'authorname', 'subject', 'committername']#TODO: Set var global. And get dict of new localnames
        if rename_fields is None:
            rename_fields = fields
        if isinstance(ref, str) or isinstance(ref, basestring):
            ref = ref.split(',')
        res = {}
        for repo in self.browse(cr, uid, ids, context=context):
            res[repo.id] = []
            fmt = "%00".join(["%("+field+")" for field in fields])
            cmd = ['for-each-ref', '--format', fmt, '--sort=-committerdate']
            cmd.extend( ref )
            git_refs = repo.git( cmd )
            if git_refs:
                git_refs = git_refs.strip()
                refs = [[decode_utf(field) for field in line.split('\x00')] for line in git_refs.split('\n')]
                for data_field in refs:
                #for refname, objectname, committerdate, author, subject, committername in refs:
                    res[repo.id].append( dict(zip(rename_fields, data_field)) )
        return res

    def fetch_git(self, cr, uid, ids, refs=REFS_FETCH_DEFAULT, context=None):
        if context is None:
            context = {}
        clone_only = context.get('clone_only', False)
        repo_updated_ids = []
        for repo in self.browse(cr, uid, ids, context=context):
            _logger.debug('repo %s fetch branches', repo.name)
            if not os.path.isdir(os.path.join(repo.path)):
                os.makedirs(repo.path)
            if not os.path.isdir(os.path.join(repo.path, 'refs')):
                #repo.git(['clone', '--bare', repo.name])#TODO: Get clone with this function
                try:
                    run(['git', 'clone', '--bare', repo.name, repo.path])
                except:
                    #TODO: Get exception of lost connection... no internet
                    pass
                repo_updated_ids.append(repo.id)
            else:
                if not clone_only:
                    for ref in refs:
                        try:
                            repo.git(['fetch', '-p', 'origin', ref])
                        except:
                            #TODO: Get exception of lost connection... no internet
                            pass
                        repo_updated_ids.append(repo.id)
        return repo_updated_ids

    def update(self, cr, uid, ids, context=None):
        #All active repo get last version and new branches
        if context is None:
            context = {}
        all_repo_ids = self.pool.get('runbot.repo').search(cr, uid, [], context=context)

        context2 = context.copy()
        context2.update({'clone_only': True})
        repo_updated_ids = self.fetch_git(cr, uid, all_repo_ids, context=context2)
        new_branch_ids = self.create_branches(cr, uid, repo_updated_ids, context=context)
        
        #create build from prebuild configuration
        self.create_build_from_prebuild(cr, uid, None, context=context)
        
        #Continue with normal process
        return super(runbot_repo, self).update(cr, uid, ids, context=context)

    def create_build_from_prebuild(self, cr, uid, ids=None, context=None):
        if context is None:
            context = {}
        prebuild_pool = self.pool.get('runbot.prebuild')
        build_pool = self.pool.get('runbot.build')
        prebuild_line_pool = self.pool.get('runbot.prebuild.branch')

        prebuild_sticky_ids = prebuild_pool.search(cr, uid, [('sticky', '=', True)], context=context)

        #Search repo used into prebuild from sticky build (and check pr or check new commit) to update
        prebuild_line_sticky_ids = prebuild_line_pool.search(cr, uid, [
                '&', ('prebuild_id', 'in', prebuild_sticky_ids),
                '|', ('check_pr', '=', True),
                ('check_new_commit', '=', True),
            ], context=context)
        prebuild_line_datas = prebuild_line_pool.read(cr, uid, prebuild_line_sticky_ids, ['repo_id'], context=context)
        repo_ids = list( set( [prebuild_line_data['repo_id'][0] for prebuild_line_data in prebuild_line_datas] ) )

        #fetch repo
        self.fetch_git(cr, uid, repo_ids, context=context)

        #create build from prebuild of new commit
        prebuild_ids = prebuild_pool.create_prebuild_new_commit(cr, uid, prebuild_sticky_ids, context=context)

        #create build from prebuild of pr
        prebuild_pr_ids = prebuild_pool.create_build_pr(cr, uid, prebuild_sticky_ids, context=context)

        #Get build_ids with prebuild_id set it. And assign in context for use it in scheduler function
        builds_from_prebuild_ids = build_pool.search(cr, uid, [('prebuild_id', '<>', False)], context=context)
        context['build_ids'] = builds_from_prebuild_ids

    def get_branch_repo(self, cr, uid, ids, context=None):
        '''
        Method to get the branches that have assigned the repo
        '''
        if context is None:
            context = {}
        branch_obj = self.pool.get('runbot.branch')
        branch_ids = branch_obj.search(cr, uid, [('repo_id', 'in', ids)], context=context)
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
        Method to get the runbot prebuilds that have assigned the repo in yours lines
        '''
        if context is None:
            context = {}
        branch_obj = self.pool.get('runbot.branch')
        prebuild_bra_obj = self.pool.get('runbot.prebuild.branch')
        branch_ids = branch_obj.search(cr, uid, [('repo_id', 'in', ids)], context=context)
        pre_bra_ids = prebuild_bra_obj.search(cr, uid, [('branch_id', 'in', branch_ids)])
        prebuild_ids = []
        for pre_bra in prebuild_bra_obj.browse(cr, uid, pre_bra_ids, context=context):
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

    @http.route(['/runbot',
                 '/runbot/repo/<model("runbot.repo"):repo>',
                 '/runbot/team/<model("runbot.team"):team>'],
                 type='http', auth="public", website=True)
    def repo(self, repo=None, team=None, search='', limit='100', refresh='', **post):
        registry, cr, uid = request.registry, request.cr, SUPERUSER_ID
        res = super(RunbotController, self).repo(repo=repo, search=search, limit=limit, refresh=refresh, **post)

        branch_obj = registry['runbot.branch']
        build_obj = registry['runbot.build']
        team_obj = registry['runbot.team']
        repo_obj = registry['runbot.repo']
        team_ids = team_obj.search(cr, uid, [], order='id')
        teams = team_obj.browse(cr, uid, team_ids)
        res.qcontext.update({'teams':teams})
        if team:
            filters = {key: post.get(key, '1') for key in ['pending', 'testing', 'running', 'done']}
            #build_ids = build_obj.search(cr, uid, [('team_id', '=', team.id)], limit=int(limit))
            branch_ids, build_by_branch_ids = [], {}

            if True:#build_ids:
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
                build_by_branch_ids = OrderedDict( [ ((rec[0], rec[1], rec[2]), [r for r in rec[3:] if r is not None]) for rec in cr.fetchall()] )

            build_ids = flatten(build_by_branch_ids.values())
            build_dict = {build.id: build for build in build_obj.browse(cr, uid, build_ids, context=request.context) }
            def branch_info(branch, branch_dependency, prebuild):
                key = (
                    branch.id,
                    branch_dependency and branch_dependency.id or None,
                    prebuild and prebuild.id or None,
                )
                return {
                    'branch': branch,
                    'branch_dependency': branch_dependency,
                    'prebuild': prebuild,
                    'builds': [self.build_info(build_dict[build_id]) for build_id\
                               in build_by_branch_ids[ key ]
                    ]
                }

            res.qcontext.update({
                'branches': [ branch_info(
                                branch_id and branch_obj.browse(cr, uid, [branch_id], context=request.context)[0] or None,\
                                branch_dependency_id and branch_obj.browse(cr, uid, [branch_dependency_id], context=request.context)[0] or None,\
                                prebuild_id and branch_obj.browse(cr, uid, [prebuild_id], context=request.context)[0] or None,\
                         ) \
                         for branch_id, branch_dependency_id, prebuild_id in build_by_branch_ids ],
                'testing': build_obj.search_count(cr, uid, [('team_id','=',team.id), ('state','=','testing')]),
                'team': team,
                'running': build_obj.search_count(cr, uid, [('team_id','=',team.id), ('state','=','running')]),
                'pending': build_obj.search_count(cr, uid, [('team_id','=',team.id), ('state','=','pending')]),
                'qu': QueryURL('/runbot/team/'+slug(team), search=search, limit=limit, refresh=refresh, **filters),
                'filters': filters,
            })

        return res

    def build_info(self, build):
        res = super(RunbotController, self).build_info(build)
        res.update({'prebuild_id':build.prebuild_id,
                    'build':build})
        return res

    @http.route(['/runbot/build/<build_id>'], type='http', auth="public", website=True)
    def build(self, build_id=None, search=None, **post):
        registry, cr, uid = request.registry, request.cr, SUPERUSER_ID
        res = super(RunbotController, self).build(build_id=build_id, search=search, **post)
        build_brw = registry['runbot.build'].browse(cr, uid, int(build_id))
        if build_brw.team_id.name:
            res.qcontext.update({'team':build_brw.team_id})
        return res


    @http.route(['/runbot/build/<build_id>/force'], type='http', auth="public", website=True)
    def build_force(self, build_id, **post):
        registry, cr, uid = request.registry, request.cr, SUPERUSER_ID
        res = super(RunbotController, self).build_force(build_id, **post)
        build_brw = registry['runbot.build'].browse(cr, uid, int(build_id))
        if build_brw.team_id.name:
            return werkzeug.utils.redirect('/runbot/team/%s' % build_brw.team_id.id)
        else:
            return res

    @http.route(['/runbot/build/<build_id>/label/<label_id>'], type='http', auth="public", method='POST')
    def toggle_label(self, build_id=None, label_id=None, search=None, **post):
        registry, cr, uid = request.registry, request.cr, SUPERUSER_ID

        build_brw = registry['runbot.build'].browse(cr, uid, [int(build_id)])[0]
        res = super(RunbotController, self).toggle_label(build_id=build_id, label_id=label_id, search=search, **post)
        if build_brw.team_id.name:
            return werkzeug.utils.redirect('/runbot/team/%s' % build_brw.team_id.id)
        else:
            return res

