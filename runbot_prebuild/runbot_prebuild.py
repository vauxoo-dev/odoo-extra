# -*- encoding: utf-8 -*-
#TODO: license from vauxoo
from openerp.osv import fields, osv

class runbot_prebuild_branch(osv.osv):
    _name = "runbot.prebuild.branch"
    _rec_name = 'branch_id'
    
    _columns = {
        'branch_id': fields.many2one('runbot.branch', 'Branch depends', required=True, 
            ondelete='cascade', select=1),
        'check_pr': fields.boolean('Check Pull Request to this branch?', 
            help='If is True, this will check PR for this branch in this prebuild'),
        'revision_id': fields.char('SHA commit', 
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
        'main_branch_id': fields.many2one('runbot.branch', 'Main branch', required=True, 
            ondelete='cascade', select=1),
        'prebuild_branch_ids': fields.one2many('runbot.prebuild.branch', 'prebuild_id', 
            string='Branches extra dependencies', copy=True,
            help="Community addon branches which need to be present to run tests."),
        'repo_id': fields.related('main_branch_id', 'repo_id', type="many2one", 
            relation="runbot.repo", string="Repository from main branch", readonly=True, 
            store=True, ondelete='cascade', select=1),
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
    }

class runbot_build(osv.osv):
    _name = "runbot.branch"
    #TODO: get_name -> branch.repo_id.name + branch.name
    #TODO: name search -> branch.repo_id.name + branch.name
