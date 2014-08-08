# -*- encoding: utf-8 -*-
#TODO: license from vauxoo
from openerp.osv import fields, osv
import os
import shutil
import logging
import glob
import time
from openerp.addons.runbot.runbot import RunbotController
import dateutil.parser

_logger = logging.getLogger(__name__)

def mkdirs(dirs):
    for d in dirs:
        if not os.path.exists(d):
            os.makedirs(d)

def decode_utf(field):
    try:
        return field.decode('utf-8')
    except UnicodeDecodeError:
        return ''


class runbot_prebuild_branch(osv.osv):
    _name = "runbot.prebuild.branch"
    _rec_name = 'branch_id'

    _columns = {
        'branch_id': fields.many2one('runbot.branch', 'Branch', required=True,
            ondelete='cascade', select=1),
        'check_pr': fields.boolean('Check PR',
            help='If is True, this will check Pull Request for this branch in this prebuild'),
        'sha': fields.char('SHA commit', size=40,
            help='Empty=Currently version\nSHA=Get this version in builds'),
        'prebuild_id': fields.many2one('runbot.prebuild', 'Pre-Build', required=True,
            ondelete='cascade', select=1),
        'repo_id': fields.related('branch_id', 'repo_id', type="many2one",
            relation="runbot.repo", string="Repository", readonly=True, store=True,
            ondelete='cascade', select=1),
    }


class runbot_prebuild(osv.osv):
    _name = "runbot.prebuild"

    _columns = {
        'name': fields.char("Name", size=128, required=True),
        'module_branch_ids': fields.one2many('runbot.prebuild.branch', 'prebuild_id',
            string='Branches of modules', copy=True,
            help="Community addons branches which need to run tests."),
        'sticky': fields.boolean('Sticky', select=1,
            help="If True: Stay alive a instance ever. And check PR to main branch and"\
            " modules branches for make pre-builds\nIf False: Stay alive a instance only"\
            " moment and not check PR."),
        'modules': fields.char("Modules to Install", size=256,
            help="Empty is all modules availables"),
        'modules_to_exclude': fields.char("Modules to exclude", size=256,
            help="Empty is exclude none. Add modules is exclude this one. FEATURE TODO"),
        'language': fields.char("Language of instance", size=5,
            help="Language to change instance after of run test.\nFormat ll_CC<-language and country"),
        'script_prebuild': fields.text('Script Pre-Build',
            help="Script to execute before run build"),
        'script_posbuild': fields.text('Script Pos-Build',
            help="Script to execute after run build"),
        'prebuild_parent_id': fields.many2one('runbot.prebuild', 'Parent Prebuild', help="If this is a prebuild from PR this field is for set original prebuild"),
    }
    """
    def search_prebuild_pr(self, cr, uid, ids, context=None):
         #self.search(cr, uid, [('')])
        prebuild_branch_pool = self.poo.get('runbot.prebuild.branch')
        prebuild_branch_ids = prebuild_branch_pool.search(cr, uid, [('check_pr', '=', True), ('prebuild_id', '=', ids)], context=context )
        branch_id
    """
    #TODO: Add constraint that add prebuild_lines of least one main repo type
    
    def get_prebuilds_with_new_commit(self, cr, uid, ids, context=None):
        """
        Create build of sticky build with changes in your branches
        """
        build_pool = self.pool.get('runbot.build')
        build_line_pool = self.pool.get('runbot.build.line')
        repo_pool = self.pool.get('runbot.repo')
        branch_pool = self.pool.get('runbot.branch')
        for prebuild_sticky_id in ids:
            prebuild_child_ids = self.search(cr, uid, [('prebuild_parent_id', '=', prebuild_sticky_id)], context=context)
            build_ids = build_pool.search(cr, uid, [('prebuild_id', 'in', prebuild_child_ids + [prebuild_sticky_id])], context=context)
            build_line_ids = build_line_pool.search(cr, uid, [('build_id', 'in', build_ids)], context=context)
            #import pdb;pdb.set_trace()
            if build_line_ids:
                query = "SELECT branch_id, repo_id FROM runbot_build_line WHERE id IN %s GROUP BY branch_id, repo_id"
                cr.execute( query, (tuple(build_line_ids),) )
                res = cr.fetchall()
                branch_ids = [ r[0] for r in res ]
                repo_ids = [ r[1] for r in res ]
                #build_line_branch_datas = build_line_pool.read(cr, uid, build_line_ids, ['branch_id'], context=context)
                #branch_ids = list( set([build_line_branch_data['branch_id'] for build_line_branch_data in build_line_branch_datas]) )
                #for r in res:
                    #repo_pool.get_ref_data(cr, uid, [r[0]], r[1], context=None):
                #TODO: update repo_ids
                for branch in branch_pool.browse(cr, uid, branch_ids, context=context):
                    refs = repo_pool.get_ref_data(cr, uid, [branch.repo_id.id], branch.name, context=context)
                    if refs and refs[branch.repo_id.id]:
                        ref_data = refs[branch.repo_id.id][0]
                        sha = ref_data['sha']
                        build_line_with_sha_ids = build_line_pool.search(cr, uid, [('build_id', 'in', build_ids), ('sha', '=', sha)], context=context, limit=1)
                        if not build_line_with_sha_ids:
                            pass#TODO: Make it
                            #But in this point checkout should be functionallity with sha previous.
        return {}

    def create_prebuild_pr(self, cr, uid, ids, context=None):
        """
        Create prebuild from pull request with branches pr_check=True and prebuild sticky=True
        """
        new_prebuild_ids = [ ]
        branch_pool = self.pool.get('runbot.branch')
        prebuild_line_pool = self.pool.get('runbot.prebuild.branch')
        for prebuild in self.browse(cr, uid, ids, context=context):
            for prebuild_line in prebuild.module_branch_ids:
                if prebuild_line.check_pr:
                    branch_pr_ids = branch_pool.search(cr, uid, [('branch_base_id', '=', prebuild_line.branch_id.id)], context=context)
                    #Get prebuild childs
                    prebuild_child_ids = self.search(cr, uid, [('prebuild_parent_id', '=', prebuild.id)], context=context )
                    for branch_pr in branch_pool.browse(cr, uid, branch_pr_ids, context=context):
                        prebuild_pr_ids = prebuild_line_pool.search(cr, uid, [('branch_id', '=', branch_pr.id), ('prebuild_id', 'in', prebuild_child_ids)], limit=1)
                        if prebuild_pr_ids:
                            #if exist prebuild of pr no create new one
                            continue
                        #If not exist prebuild of this pr then create one
                        new_prebuild_pr_id = self.copy(cr, uid, prebuild.id, {
                            'name': prebuild.name + ' - ' + branch_pr.complete_name,
                            'prebuild_parent_id': prebuild.id,
                            'sticky': False,
                        }, context=context)
                        
                        #Search prebuild lines for set check_pr = False. For not check pr of childs.
                        new_prebuild_line_ids = prebuild_line_pool.search(cr, uid, [
                            ('prebuild_id', '=', new_prebuild_pr_id),
                        ], context=context)
                        for new_prebuild_line in prebuild_line_pool.browse(cr, uid, new_prebuild_line_ids, context):
                            new_data = { 'check_pr': False}
                            if new_prebuild_line.branch_id.id == prebuild_line.branch_id.id:
                                #Replace branch base by branch pr in new pre-build
                                new_data.update({'branch_id': branch_pr.id})
                            prebuild_line_pool.write(cr, uid, [new_prebuild_line.id], new_data, context=context)
                        new_prebuild_ids.append( new_prebuild_pr_id )
        return new_prebuild_ids

    def create_build(self, cr, uid, ids, context=None):
        if context is None:
            context = {}
        repo_obj = self.pool.get('runbot.repo')
        build_obj = self.pool.get('runbot.build')
        build_ids = []
        for prebuild in self.browse(cr, uid, ids, context=context):
            module_repositories = [prebuild_branch.branch_id.repo_id for prebuild_branch in prebuild.module_branch_ids]

            #Update repository but no create default build
            context.update({'create_builds': False})
            for prebuild_branch in prebuild.module_branch_ids:
                try:
                    repo_obj.update_git(cr, uid, prebuild_branch.branch_id.repo_id, prebuild=prebuild, context=context)
                except:
                    #TODO: warning if no internet
                    pass

            build_info = {
                'branch_id': prebuild_branch.branch_id.id,#Last branch of for
                'name': prebuild.name,#TODO: Get this value
                'author': prebuild.name,#TODO: Get this value
                'subject': prebuild.name,#TODO: Get this value
                'date': time.strftime("%Y-%m-%d %H:%M:%S"),#TODO: Get this value
                'modules': prebuild.modules,
                'prebuild_id': prebuild.id,#Important field for custom build and custom checkout
            }
            build_id = build_obj.create(cr, uid, build_info)
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
        'prebuild_id': fields.many2one('runbot.prebuild', string='Runbot Pre-Build',
            required=False, help="This is the origin of instance data."),
        'line_ids': fields.one2many('runbot.build.line', 'build_id',
            string='Build branches lines', readonly=True),
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
            if build.prebuild_id:
                # starts from scratch
                if os.path.isdir(build.path()):
                    shutil.rmtree(build.path())
                _logger.debug('Creating build in path "%s"'%( build.path() ))

                # runbot log path
                mkdirs([build.path("logs"), build.server('addons')])

                # v6 rename bin -> openerp
                if os.path.isdir(build.path('bin/addons')):
                    shutil.move(build.path('bin'), build.server())
                
                for prebuild_line in build.prebuild_id.module_branch_ids:
                    if prebuild_line.branch_id.repo_id.type == 'main':
                        path = build.path()
                    elif prebuild_line.branch_id.repo_id.type == 'module':
                        path = build.server("addons")
                    else:
                        pass #TODO: raise error
                    prebuild_line.branch_id.repo_id.git_export(prebuild_line.branch_id.name, path)
                    
                    ref_datas = prebuild_line.branch_id.repo_id.get_ref_data(prebuild_line.branch_id.name)
                    ref_data = ref_datas[prebuild_line.branch_id.repo_id.id][0]
                    
                    build_line_ids = build_line_obj.create(cr, uid, {
                        'build_id': build.id,
                        'branch_id': prebuild_line.branch_id.id,
                        'sha': ref_data['sha'],
                        'author': ref_data['author'],
                        'subject': ref_data['subject'],
                        'date': ref_data['date'],
                    }, context=context)
                
                # move all addons to server addons path
                for module in glob.glob( build.path('addons/*') ):
                    shutil.move(module, build.server('addons'))

    def checkout(self, cr, uid, ids, context=None):
        for build in self.browse(cr, uid, ids, context=context):
            if not build.prebuild_id:
                #TODO: Split branches for use same function "checkout_params"
                return super(runbot_build, self).checkout(cr, uid, ids, context=context)
            else:
                #module_branch_ids = [module_branch_id.branch_id.id for module_branch_id in build.prebuild_id.module_branch_ids]
                #self.checkout_params(cr, uid, [build.id], module_branch_ids=module_branch_ids, modules_to_test=build.prebuild_id.modules, context=context)
                self.checkout_prebuild(cr, uid, [build.id], context=context)

class runbot_build_line(osv.osv):
    _name = 'runbot.build.line'
    _rec_name = 'sha'

    _columns = {
        'build_id': fields.many2one('runbot.build', 'Build', required=True,
            ondelete='cascade', select=1),
        'branch_id': fields.many2one('runbot.branch', 'Branch', required=True,
            ondelete='cascade', select=1),
        'sha': fields.char('SHA commit', size=40,
            help='Version of commit or sha', required=True),
        'date': fields.datetime('Commit date'),
        'author': fields.char('Author'),
        'subject': fields.text('Subject'),
        'repo_id': fields.related('branch_id', 'repo_id', type="many2one", relation="runbot.repo", string="Repository", readonly=True, store=True, ondelete='cascade', select=1),
    }

class runbot_repo(osv.osv):
    _inherit = "runbot.repo"

    def get_ref_data(self, cr, uid, ids, ref, context=None):
        res = {}
        for repo in self.browse(cr, uid, ids, context=context):
            res[repo.id] = []
            fields = ['refname','objectname','committerdate:iso8601','authorname','subject']
            fmt = "%00".join(["%("+field+")" for field in fields])
            git_refs = repo.git(['for-each-ref', '--format', fmt, '--sort=-committerdate', ref])
            if git_refs:
                git_refs = git_refs.strip()
                refs = [[decode_utf(field) for field in line.split('\x00')] for line in git_refs.split('\n')]
                for name, sha, date, author, subject in refs:
                    res[repo.id].append({
                        'name': name,
                        'sha': sha,
                        'date': dateutil.parser.parse(date[:19]),
                        'author': author,
                        'subject': subject
                    })
        return res

    def cron(self, cr, uid, ids=None, context=None):
        prebuild_pool = self.pool.get('runbot.prebuild')
        prebuild_sticky_ids = prebuild_pool.search(cr, uid, [('sticky', '=', True)], context=context)
        prebuild_pr_ids = prebuild_pool.create_prebuild_pr(cr, uid, prebuild_sticky_ids, context=context)
        #build_ids = prebuild_pool.create_build(cr, uid, prebuild_pr_ids, context=context)
        prebuild_pool.get_prebuilds_with_new_commit(cr, uid, prebuild_sticky_ids, context=context)
        return super(runbot_repo, self).cron(cr, uid, ids, context=context)

class RunbotController(RunbotController):

    def build_info(self, build):
        res = super(RunbotController, self).build_info(build)
        res.update({'prebuild_id':build.prebuild_id,
                    'build':build})
        return res
