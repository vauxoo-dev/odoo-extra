# -*- encoding: utf-8 -*-

import datetime
import fcntl
import glob
import hashlib
import itertools
import logging
import operator
import os
import re
import resource
import shutil
import signal
import simplejson
import socket
import subprocess
import sys
import time
from collections import OrderedDict

import dateutil.parser
import requests
from matplotlib.font_manager import FontProperties
from matplotlib.textpath import TextToPath
import werkzeug

import openerp
from openerp import http, SUPERUSER_ID
from openerp.http import request
from openerp.osv import fields, osv
from openerp.tools import config, appdirs
from openerp.addons.website.models.website import slug
from openerp.addons.website_sale.controllers.main import QueryURL
from openerp.service.db import exp_drop, _create_empty_database

_logger = logging.getLogger(__name__)

#----------------------------------------------------------
# Runbot Const
#----------------------------------------------------------

_re_error = r'^(?:\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3} \d+ (?:ERROR|CRITICAL) )|(?:Traceback \(most recent call last\):)$'
_re_warning = r'^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3} \d+ WARNING '
_re_job = re.compile('job_\d')


#----------------------------------------------------------
# RunBot helpers
#----------------------------------------------------------

def log(*l, **kw):
    out = [i if isinstance(i, basestring) else repr(i) for i in l] + \
          ["%s=%r" % (k, v) for k, v in kw.items()]
    _logger.debug(' '.join(out))

def dashes(string):
    """Sanitize the input string"""
    for i in '~":\'':
        string = string.replace(i, "")
    for i in '/_. ':
        string = string.replace(i, "-")
    return string

def mkdirs(dirs):
    for d in dirs:
        if not os.path.exists(d):
            os.makedirs(d)

def grep(filename, string):
    if os.path.isfile(filename):
        return open(filename).read().find(string) != -1
    return False

def rfind(filename, pattern, excludes=None, excludes_wtraceback=None, error_count_expected=None):
    """Determine in something in filename matches the pattern"""
    if excludes is None:
        excludes = []
    if error_count_expected is None:
        error_count_expected = 0
    if excludes_wtraceback is None:
        excludes_wtraceback = []
    if os.path.isfile(filename):
        regexp = re.compile(pattern, re.M)
        with open(filename, 'r') as f:
            data = ""
            exclude_next_traceback = True
            for line in f.readlines():
                if exclude_next_traceback and "Traceback (most recent call last):" in line:
                    exclude_next_traceback = False
                    continue
                if any([exclude in line for exclude in excludes]):
                    continue
                if any([exclude_wtraceback in line for exclude_wtraceback in excludes_wtraceback]):
                    exclude_next_traceback = True
                    continue
                data += line
            error_list = regexp.findall(data)
            if len(error_list) != error_count_expected:
                return True
    return False

def lock(filename):
    fd = os.open(filename, os.O_CREAT | os.O_RDWR, 0600)
    fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

def locked(filename):
    result = False
    try:
        fd = os.open(filename, os.O_CREAT | os.O_RDWR, 0600)
        try:
            fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except IOError:
            result = True
        os.close(fd)
    except OSError:
        result = False
    return result

def nowait():
    signal.signal(signal.SIGCHLD, signal.SIG_IGN)

def run(l, env=None):
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

def now():
    return time.strftime(openerp.tools.DEFAULT_SERVER_DATETIME_FORMAT)

def dt2time(datetime):
    """Convert datetime to time"""
    return time.mktime(time.strptime(datetime, openerp.tools.DEFAULT_SERVER_DATETIME_FORMAT))

def s2human(time):
    """Convert a time in second into an human readable string"""
    for delay, desc in [(86400,'d'),(3600,'h'),(60,'m')]:
        if time >= delay:
            return str(int(time / delay)) + desc
    return str(int(time)) + "s"

def flatten(list_of_lists):
    return list(itertools.chain.from_iterable(list_of_lists))

def decode_utf(field):
    try:
        return field.decode('utf-8')
    except UnicodeDecodeError:
        return ''

def uniq_list(l):
    return OrderedDict.fromkeys(l).keys()

def fqdn():
    return socket.gethostname()

#----------------------------------------------------------
# RunBot Models
#----------------------------------------------------------

class runbot_repo(osv.osv):
    _name = "runbot.repo"
    _order = 'id'

    def _get_path(self, cr, uid, ids, field_name, arg, context=None):
        root = self.root(cr, uid)
        result = {}
        for repo in self.browse(cr, uid, ids, context=context):
            name = repo.name
            for i in '@:/':
                name = name.replace(i, '_')
            result[repo.id] = os.path.join(root, 'repo', name)
        return result

    def _get_url_info(self, cr, uid, ids, field_names, arg, context=None):
        result = {}
        for repo in self.browse(cr, uid, ids, context=context):
            result[repo.id] = {
                'host': False,
                'owner': False,
                'repo': False,
                'base': False,
                'host_driver': False,
                'host_url': False,
                'url': repo.name,
            }
            if os.path.isdir( repo.name ):
                result[repo.id]['host_driver'] = 'localpath'
                result[repo.id]['host_url'] = 'localhost'
                result[repo.id]['url'] = '%s%s'%( 'file://', repo.name )
            name = re.sub('.+@', '', repo.name)
            name = name.replace(':','/')
            result[repo.id]['base'] = name
            regex = "(?P<host>(git@|https://)([\w\.@]+)(/|:))(?P<owner>[~\w,\-,\_]+)/(?P<repo>[\w,\-,\_]+)(.git){0,1}((/){0,1})"
            match_object = re.search( regex, repo.name )
            if match_object:
                result[repo.id]['host'] = match_object.group("host")
                result[repo.id]['owner'] = match_object.group("owner")
                result[repo.id]['repo'] = match_object.group("repo")
                if 'github.com' in result[repo.id]['host']:
                    result[repo.id]['host_driver'] = 'github'
                    result[repo.id]['host_url'] = 'github.com'
                    result[repo.id]['url'] = '/'.join( [ 'https://', result[repo.id]['host_url'], result[repo.id]['owner'], result[repo.id]['repo'] ] )
                elif 'bitbucket.org' in result[repo.id]['host']:
                    result[repo.id]['host_driver'] = 'bitbucket'
                    result[repo.id]['host_url'] = 'bitbucket.org'
                    result[repo.id]['url'] = '/'.join( [ 'https://', result[repo.id]['host_url'], result[repo.id]['owner'], result[repo.id]['repo'] ] )
                elif 'launchpad.net' in result[repo.id]['host']:
                    result[repo.id]['host_driver'] = 'launchpad'
                    result[repo.id]['host_url'] = 'launchpad.net'
                    result[repo.id]['url'] = '/'.join( [ 'https://', result[repo.id]['host_url'], result[repo.id]['owner'], result[repo.id]['repo'] ] )
                else:
                    pass
                    #You can inherit this function for add more host's
        return result

    _columns = {
        'name': fields.char('Repository', required=True),
        'path': fields.function(_get_path, type='char', string='Directory', readonly=1),
        'base': fields.function(_get_url_info, type='char', string='Base URL', readonly=1, multi='url_info', store=True),
        'host': fields.function(_get_url_info, type='char', string='Host from URL', readonly=1, multi='url_info', store=True),
        'owner': fields.function(_get_url_info, type='char', string='Owner from URL', readonly=1, multi='url_info', store=True),
        'repo': fields.function(_get_url_info, type='char', string='Repo from URL', readonly=1, multi='url_info', store=True),
        'host_driver': fields.function(_get_url_info, type='char', string='Host driver from URL', readonly=1, multi='url_info', store=True),
        'host_url': fields.function(_get_url_info, type='char', string='URL host', readonly=1, multi='url_info', store=True),
        'url': fields.function(_get_url_info, type='char', string='URL repo', readonly=1, multi='url_info', store=True),
        'testing': fields.integer('Concurrent Testing'),
        'running': fields.integer('Concurrent Running'),
        'jobs': fields.char('Jobs'),
        'nginx': fields.boolean('Nginx'),
        'auto': fields.boolean('Auto'),
        'duplicate_id': fields.many2one('runbot.repo', 'Duplicate repo', help='Repository for finding duplicate builds'),
        'modules': fields.char("Modules to Install", help="Comma-separated list of modules to install and test."),
        'dependency_ids': fields.many2many(
            'runbot.repo', 'runbot_repo_dep_rel',
            id1='dependant_id', id2='dependency_id',
            string='Extra dependencies',
            help="Community addon repos which need to be present to run tests."),
        'token': fields.char("Github token"),
        'active': fields.boolean('Active'),
        'type': fields.selection([
            ('main', 'Main'),
            ('module', 'Modules'),
          ], required=True, string="Type", help="Modules: Copy to addons path\nMain: Copy to principal path"),
        'group_ids': fields.many2many('res.groups', string='Limited to groups'),
    }

    _defaults = {
        'testing': 1,
        'running': 1,
        'auto': True,
        'active': True,
    }

    def domain(self, cr, uid, context=None):
        domain = self.pool.get('ir.config_parameter').get_param(cr, uid, 'runbot.domain', fqdn())
        return domain

    def root(self, cr, uid, context=None):
        """Return root directory of repository"""
        default = os.path.join(os.path.dirname(__file__), 'static')
        return self.pool.get('ir.config_parameter').get_param(cr, uid, 'runbot.root', default)

    def git(self, cr, uid, ids, cmd, context=None):
        """Execute git command cmd"""
        for repo in self.browse(cr, uid, ids, context=context):
            cmd = ['git', '--git-dir=%s' % repo.path] + cmd
            _logger.info("git: %s", ' '.join(cmd))
            try:
                return subprocess.check_output(cmd)
            except: return None

    def git_export(self, cr, uid, ids, treeish, dest, context=None):
        for repo in self.browse(cr, uid, ids, context=context):
            _logger.debug('checkout %s %s %s', repo.name, treeish, dest)
            p1 = subprocess.Popen(['git', '--git-dir=%s' % repo.path, 'archive', treeish], stdout=subprocess.PIPE)
            p2 = subprocess.Popen(['tar', '-xC', dest], stdin=p1.stdout, stdout=subprocess.PIPE)
            p1.stdout.close()  # Allow p1 to receive a SIGPIPE if p2 exits.
            p2.communicate()[0]

    def github(self, cr, uid, ids, url, payload=None, ignore_errors=False, context=None):
        """Return a http request to be sent to github"""
        for repo in self.browse(cr, uid, ids, context=context):
            try:
                if repo.host_driver != 'github':
                    raise Exception('Repository does not have a driver to use github')
                if not repo.token:
                    raise Exception('Repository does not have a token to authenticate')
                url = url.replace(':owner', repo.owner)
                url = url.replace(':repo', repo.repo)
                url = 'https://api.%s%s' % (repo.host_url, url)
                session = requests.Session()
                session.auth = (repo.token,'x-oauth-basic')
                session.headers.update({'Accept': 'application/vnd.github.she-hulk-preview+json'})
                if payload:
                    response = session.post(url, data=simplejson.dumps(payload))
                else:
                    response = session.get(url)
                return response.json()
            except Exception:
                if ignore_errors:
                    _logger.exception('Ignored github error %s %r', url, payload)
                else:
                    raise

    def update(self, cr, uid, ids=None, context=None):
        if ids is None:
	    ids = self.search(cr, uid, [('auto', '=', True)], context=context)
        for repo in self.browse(cr, uid, ids, context=context):
            self.update_git(cr, uid, repo, context=context)

    def update_git(self, cr, uid, repo, context=None):
        _logger.debug('repo %s updating branches', repo.name)
        Build = self.pool['runbot.build']
        Branch = self.pool['runbot.branch']
        # days to check branches
        icp = self.pool['ir.config_parameter']
        days_to_check = int(icp.get_param(cr, uid, 'runbot.days_to_check', default=30))

        if not os.path.isdir(os.path.join(repo.path)):
            os.makedirs(repo.path)
        if not os.path.isdir(os.path.join(repo.path, 'refs')):
            run(['git', 'clone', '--bare', repo.name, repo.path])
        else:
            repo.git(['gc', '--auto', '--prune=all'])
            repo.git(['fetch', '-p', 'origin', '+refs/heads/*:refs/heads/*'])
            repo.git(['fetch', '-p', 'origin', '+refs/pull/*/head:refs/pull/*'])

        fields = ['refname','objectname','committerdate:iso8601','authorname','authoremail','subject','committername','committeremail']
        fmt = "%00".join(["%("+field+")" for field in fields])
        #Split in two lines to detect bzr2git heads correctly. First real head and later mp.
        git_refs = repo.git(['for-each-ref', '--format', fmt, '--sort=refname',\
                             'refs/heads'])
        git_refs += repo.git(['for-each-ref', '--format', fmt, \
            '--sort=-committerdate', 'refs/pull'])
        git_refs = git_refs.strip()

        refs = [[decode_utf(field) for field in line.split('\x00')] for line in git_refs.split('\n')]

        for name, sha, date, author, author_email, subject, committer, committer_email in refs:

            # skip build for old branches
            if dateutil.parser.parse(date[:19]) + datetime.timedelta(days_to_check) < datetime.datetime.now() \
               and not name.startswith('refs/heads'):
                _logger.debug("skip 'update_git' for old branches")
                continue


            # create or get branch
            branch_ids = Branch.search(cr, uid, [('repo_id', '=', repo.id), ('name', '=', name)])
            if branch_ids:
                branch_id = branch_ids[0]
            else:
                _logger.debug('repo %s found new branch %s', repo.name, name)
                branch_id = Branch.create(cr, uid, {'repo_id': repo.id, 'name': name})
            branch = Branch.browse(cr, uid, [branch_id], context=context)[0]


            # create build (and mark previous builds as skipped) if not found
            build_ids = Build.search(cr, uid, [('branch_id', '=', branch.id), ('name', '=', sha)])
            if not build_ids:
                if not branch.sticky:
                    to_be_skipped_ids = Build.search(cr, uid, [('branch_id', '=', branch.id), ('state', '=', 'pending')])
                    Build.skip(cr, uid, to_be_skipped_ids)

                _logger.debug('repo %s branch %s new build found revno %s', branch.repo_id.name, branch.name, sha)
                build_info = {
                    'branch_id': branch.id,
                    'name': sha,
                    'author': author,
                    'author_email': author_email,
                    'committer': committer,
                    'committer_email': committer_email,
                    'subject': subject,
                    'date': dateutil.parser.parse(date[:19]),
                    'modules': ','.join(filter(None, [branch.repo_id.modules, branch.modules])),
                }
                #Build.create(cr, uid, build_info) # Force to use prebuild module

        # skip old builds (if their sequence number is too low, they will not ever be built)
        skippable_domain = [('repo_id', '=', repo.id), ('state', '=', 'pending')]
        icp = self.pool['ir.config_parameter']
        running_max = int(icp.get_param(cr, uid, 'runbot.running_max', default=75))
        to_be_skipped_ids = Build.search(cr, uid, skippable_domain, order='sequence desc', offset=running_max)
        Build.skip(cr, uid, to_be_skipped_ids)

    def scheduler(self, cr, uid, ids=None, context=None):
        if context is None:
            context = {}
        build_ids = context.get('build_ids', []) or []
        icp = self.pool['ir.config_parameter']
        workers = int(icp.get_param(cr, uid, 'runbot.workers', default=6))
        running_max = int(icp.get_param(cr, uid, 'runbot.running_max', default=75))
        host = fqdn()

        Build = self.pool['runbot.build']
        domain = []
        if build_ids:
            domain.append( ('id', 'in', build_ids) )
        if ids:
            domain.append( ('repo_id', 'in', ids) )
        if len(domain) == 2:
            domain.insert(0, '|')
        #domain = [('repo_id', 'in', ids)]#new change: TODO: Now not receive build_ids. This is used in our custom modules?
        domain_host = domain + [('host', '=', host)]

        # schedule jobs (transitions testing -> running, kill jobs, ...)
        build_ids = Build.search(cr, uid, domain_host + [('state', 'in', ['testing', 'running'])])
        Build.schedule(cr, uid, build_ids)

        # launch new tests
        testing = Build.search_count(cr, uid, domain_host + [('state', '=', 'testing')])
        pending = Build.search_count(cr, uid, domain + [('state', '=', 'pending')])

        while testing < workers and pending > 0:

            # find sticky pending build if any, otherwise, last pending (by id, not by sequence) will do the job
            pending_ids = Build.search(cr, uid, domain + [('state', '=', 'pending'), ('branch_id.sticky', '=', True)], limit=1)
            if not pending_ids:
                pending_ids = Build.search(cr, uid, domain + [('state', '=', 'pending')], order="sequence", limit=1)

            pending_build = Build.browse(cr, uid, pending_ids[0])
            pending_build.schedule()

            # compute the number of testing and pending jobs again
            testing = Build.search_count(cr, uid, domain_host + [('state', '=', 'testing')])
            pending = Build.search_count(cr, uid, domain + [('state', '=', 'pending')])

        # terminate and reap doomed build
        build_ids = Build.search(cr, uid, domain_host + [('state', '=', 'running')])
        # sort builds: the last build of each sticky branch then the rest
        sticky = {}
        non_sticky = []
        for build in Build.browse(cr, uid, build_ids):
            if build.branch_id.sticky and build.branch_id.id not in sticky:
                sticky[build.branch_id.id] = build.id
            else:
                non_sticky.append(build.id)
        build_ids = sticky.values()
        build_ids += non_sticky
        # terminate extra running builds
        Build.kill(cr, uid, build_ids[running_max:])
        Build.reap(cr, uid, build_ids)

    def reload_nginx(self, cr, uid, context=None):
        settings = {}
        settings['port'] = config['xmlrpc_port']
        nginx_dir = os.path.join(self.root(cr, uid), 'nginx')
        settings['nginx_dir'] = nginx_dir
        ids = self.search(cr, uid, [('nginx','=',True)], order='id')
        if ids:
            build_ids = self.pool['runbot.build'].search(cr, uid, [('repo_id','in',ids), ('state','=','running')])
            settings['builds'] = self.pool['runbot.build'].browse(cr, uid, build_ids)

            nginx_config = self.pool['ir.ui.view'].render(cr, uid, "runbot.nginx_config", settings)
            mkdirs([nginx_dir])
            open(os.path.join(nginx_dir, 'nginx.conf'),'w').write(nginx_config)
            try:
                _logger.debug('reload nginx')
                pid = int(open(os.path.join(nginx_dir, 'nginx.pid')).read().strip(' \n'))
                os.kill(pid, signal.SIGHUP)
            except Exception:
                _logger.debug('start nginx')
                run([openerp.tools.find_in_path("nginx"), '-p', os.path.join(nginx_dir, ''), '-c', 'nginx.conf'])

    def killall(self, cr, uid, ids=None, context=None):
        # kill switch
        Build = self.pool['runbot.build']
        #build_ids = Build.search(cr, uid, [('state', 'not in', ['done', 'pending']), ('repo_id', 'in', ids)])
        #Build.terminate(cr, uid, build_ids)
        #Build.reap(cr, uid, build_ids)
        build_ids = Build.search(cr, uid, [('state', 'not in', ['done', 'pending']), ('repo_id', 'in', ids)])
        Build.kill(cr, uid, build_ids)

    def cron(self, cr, uid, ids=None, context=None):
        ids = self.search(cr, uid, [('auto', '=', True)], context=context)
        #self.update(cr, uid, ids, context=context)  #Created by other cron
        self.scheduler(cr, uid, ids, context=context)
        self.reload_nginx(cr, uid, context=context)

class runbot_branch(osv.osv):
    _name = "runbot.branch"
    _order = 'name'

    def _get_branch_data(self, cr, uid, ids, field_names, arg, context=None):
        res = {}
        for branch in self.browse(cr, uid, ids, context=context):
            res[branch.id] = {
                'branch_base_name': False,
                'branch_base_id': False,
                'branch_name': False,
                'branch_url': False,
                'complete_name': False,
            }
            if 'branch_name' in field_names or 'branch_url' in field_names or 'complete_name' in field_names:
                res[branch.id]['branch_name'] = branch.name.split('/')[-1]

                owner_name = branch.repo_id.owner or os.path.basename(\
                    os.path.dirname(branch.repo_id.name))
                repo_name = branch.repo_id.repo or os.path.basename(branch.repo_id.name)
                res[branch.id]['complete_name'] = '/'.join( map( lambda item: item or '',\
                    [owner_name, repo_name, res[branch.id]['branch_name'] or '']))

                if branch.repo_id.host_driver == 'github':
                    if re.match('^[0-9]+$', res[branch.id]['branch_name']):
                        res[branch.id]['branch_url'] = "%s/pull/%s" % (branch.repo_id.url, res[branch.id]['branch_name'])
                    else:
                        res[branch.id]['branch_url'] = "%s/tree/%s" % (branch.repo_id.url, res[branch.id]['branch_name'])
                elif branch.repo_id.host_driver == 'bitbucket':
                    if re.match('^[0-9]+$', res[branch.id]['branch_name'] or ''):
                        res[branch.id]['branch_url'] = False#ToDo: Process PR branch
                    else:
                        res[branch.id]['branch_url'] = "%s/src/?at=%s" % (branch.repo_id.url, res[branch.id]['branch_name'])
                elif branch.repo_id.host_driver == 'launchpad':
                    res[branch.id]['branch_url'] = branch.repo_id.name.\
                        replace('https://code.launchpad.net/',\
                        'http://bazaar.launchpad.net/').rstrip('/') + '/' + \
                        branch.name + '/' + 'files'
                else:
                    pass#Add inherit function for add more host

            if 'branch_base_name' in field_names or 'branch_base_id' in field_names:
                #Start section to check branches from launchpad bzr2git
                branch_name = self._get_branch_data(cr, uid, [branch.id], ['branch_name'], arg=arg, context=context)[branch.id]['branch_name']#No saved into database
                regex = "(?P<branch_base>(\w|\.|\d)+)(-)(MP)(?P<mp_number>(\d)+)"
                match_object = re.search( regex, branch_name)
                if match_object:
                    branch_base_name = 'refs/heads/' \
                        + match_object.group("branch_base")
                    branch_head_id = self.search(cr, uid, [
                            ('repo_id.id', '=', branch.repo_id.id),
                            ('name', '=', branch_base_name),
                            ('id', '<>', branch.id),
                        ], limit=1)
                    branch_head_id = branch_head_id and branch_head_id[0] or False
                    if branch_head_id:
                        res[branch.id]['branch_base_id'] = branch_head_id
                        res[branch.id]['branch_base_name'] = branch_base_name
                #end section to check branches from launchpad bzr2git
                elif branch.name.startswith('refs/pull/'):
                    merged = False
                    if branch.repo_id.host_driver == 'github' and branch.repo_id.token:
                        #using github for get branch_base from pr branch
                        pull_number = branch.name[len('refs/pull/'):]
                        pr = branch.repo_id.github('/repos/:owner/:repo/pulls/%s' % pull_number)
                        if 'Not Found' == pr.get('message'):
                            #Search into PR merged
                            pr = branch.repo_id.github('/repos/:owner/:repo/pulls/%s/merge' % pull_number)
                            if pr.has_key('base'):
                                merged = True
                        if pr.has_key('base'):
                            res[branch.id]['branch_base_name'] = pr['base']['ref']
                    else:
                        #using local branch for get branch_base from pr branch
                        branch_head_ids = self.search(cr, uid, [
                            ('repo_id.id', '=', branch.repo_id.id),
                            ('name', 'like', 'refs/heads/%'),
                            ('id', '<>', branch.id),
                        ])
                        branch_head_names = self._get_branch_data(cr, uid, branch_head_ids, ['branch_name'], arg=arg, context=context)#No saved into database
                        common_refs = {}
                        for branch_head_name in branch_head_names:
                            bhn = branch_head_names[branch_head_name]['branch_name']
                            if bhn:
                                commit = branch.repo_id.git(['merge-base', branch.name, bhn]).strip()
                                if commit:
                                    cmd = ['log', '-1', '--format=%cd', '--date=iso', commit]
                                    common_refs[ bhn ] = branch.repo_id.git(cmd).strip()
                        if common_refs:
                            name = sorted(common_refs.iteritems(), key=operator.itemgetter(1), reverse=True)[0][0]
                            res[branch.id]['branch_base_name'] = name

                    if res[branch.id]['branch_base_name']:
                        branch_base_ids = branch.search([
                                ('repo_id', '=', branch.repo_id.id),
                                ('name', '=', 'refs/heads/%s'%( res[branch.id]['branch_base_name'] ) ),
                            ])
                        if branch_base_ids:
                            res[branch.id]['branch_base_id'] = branch_base_ids[0].id
                            #if merged:
                                #self.write(cr, uid, branch.id, {'state': 'merged'})#TODO: Check stability of write into fields.function
        return res

    def _get_branch_from_repo(self, cr, uid, repo_ids, context=None):
        branch_pool = self.pool.get('runbot.branch')#We need pool.get because is other self source
        branch_ids = branch_pool.search(cr, uid, [('repo_id', 'in', repo_ids)], context=context)
        return branch_ids

    _columns = {
        'repo_id': fields.many2one('runbot.repo', 'Repository', required=True, ondelete='cascade', select=1),
        'name': fields.char('Ref Name', required=True),
        'branch_name': fields.function(_get_branch_data, type='char', string='Branch', multi='title_info', readonly=1, store=True),
        'branch_url': fields.function(_get_branch_data, type='char', string='Branch url', multi='title_info', readonly=1, store=True),
        'sticky': fields.boolean('Sticky', select=1),
        'coverage': fields.boolean('Coverage'),
        'state': fields.char('Status'),
        'branch_base_name': fields.function( _get_branch_data, type='char',
            string='Branch base name', readonly=1, multi='branch_data',
            store={
                'runbot.repo': (_get_branch_from_repo, ['token'], 10),
                'runbot.branch': (lambda self, cr, uid, ids, context: ids, ['name'], 10 )
            },
        ),
        'branch_base_id': fields.function(_get_branch_data, type='many2one', readonly=1,
            string='Branch base', relation='runbot.branch', multi='branch_data',
            store={
                'runbot.repo': (_get_branch_from_repo, ['token'], 10),
                'runbot.branch': (lambda self, cr, uid, ids, context: ids, ['name'], 10 )
            },
        ),
        'repo_owner': fields.related('repo_id', 'owner', type='char', string="Repo Owner", readonly=True, store=True, select=1),
        'repo_name': fields.related('repo_id', 'repo', type='char', string="Repo Name", readonly=True, store=True, select=1),
        'complete_name': fields.function(_get_branch_data, type='char', readonly=1, multi='branch_data',
            string='Complete Name', store=True),
        'modules': fields.char("Modules to Install", help="Comma-separated list of modules to install and test."),
    }

    _sql_constraints = [
        ('branch_name_repo_unique', 'unique (name,repo_id)', 'The name of the branch must be unique per repo !')
    ]

class runbot_build(osv.osv):
    _name = "runbot.build"
    _order = 'id desc'

    def _get_dest(self, cr, uid, ids, field_name, arg, context=None):
        r = {}
        for build in self.browse(cr, uid, ids, context=context):
            nickname = dashes(build.branch_id.name.split('/')[2])[:32]
            dest = "%05d-%s-%s" % (build.id, nickname, build.name[:6])
            r[build.id] = dashes(dest.lower())
        return r

    def _get_time(self, cr, uid, ids, field_name, arg, context=None):
        """Return the time taken by the tests"""
        r = {}
        for build in self.browse(cr, uid, ids, context=context):
            r[build.id] = 0
            if build.job_end:
                r[build.id] = int(dt2time(build.job_end) - dt2time(build.job_start))
            elif build.job_start:
                r[build.id] = int(time.time() - dt2time(build.job_start))
        return r

    def _get_age(self, cr, uid, ids, field_name, arg, context=None):
        """Return the time between job start and now"""
        r = {}
        for build in self.browse(cr, uid, ids, context=context):
            r[build.id] = 0
            if build.job_start:
                r[build.id] = int(time.time() - dt2time(build.job_start))
        return r

    def _get_domain(self, cr, uid, ids, field_name, arg, context=None):
        result = {}
        domain = self.pool['runbot.repo'].domain(cr, uid)
        for build in self.browse(cr, uid, ids, context=context):
            if build.repo_id.nginx:
                result[build.id] = "%s.%s" % (build.dest, domain)
            else:
                result[build.id] = "%s:%s" % (domain, build.port)
        return result

    _columns = {
        'branch_id': fields.many2one('runbot.branch', 'Branch', required=True, ondelete='cascade', select=1, copy=True),
        'repo_id': fields.related('branch_id', 'repo_id', type="many2one", relation="runbot.repo", string="Repository", readonly=True, store=True, ondelete='cascade', select=1),
        'name': fields.char('Revno', required=True, select=1, copy=True),
        'port': fields.integer('Port', copy=False),
        'host': fields.char('Host'),
        'dest': fields.function(_get_dest, type='char', string='Dest', readonly=1, store=True),
        'domain': fields.function(_get_domain, type='char', string='URL'),
        'date': fields.datetime('Commit date', copy=True),
        'author': fields.char('Author', copy=True),
        'author_email': fields.char('Author Email', copy=True),
        'committer': fields.char('Committer'),
        'committer_email': fields.char('Committer Email', copy=True),
        'subject': fields.text('Subject', copy=True),
        'sequence': fields.integer('Sequence', select=1, copy=False),
        'modules': fields.char("Modules to Install", copy=True),
        'result': fields.char('Result', copy=False), # ok, ko, warn, skipped, killed
        'pid': fields.integer('Pid', copy=False),
        'state': fields.char('Status', copy=False), # pending, testing, running, done, duplicate
        'job': fields.char('Job', copy=False), # job_*
        'job_start': fields.datetime('Job start', copy=False),
        'job_end': fields.datetime('Job end', copy=False),
        'job_time': fields.function(_get_time, type='integer', string='Job time', copy=False),
        'job_age': fields.function(_get_age, type='integer', string='Job age', copy=False),
        'duplicate_id': fields.many2one('runbot.build', 'Corresponding Build', copy=False),
        'branch_dependency_id': fields.many2one('runbot.branch', 'Branch depends', required=False, ondelete='cascade', select=1, copy=True),
    }

    _defaults = {
        'state': 'pending',
        'result': '',
    }

    def create(self, cr, uid, values, context=None):
        build_id = super(runbot_build, self).create(cr, uid, values, context=context)
        build = self.browse(cr, uid, build_id)
        extra_info = {'sequence' : build_id}

        # detect duplicate
        domain = [
            ('repo_id','=',build.repo_id.duplicate_id.id),
            ('name', '=', build.name),
            ('duplicate_id', '=', False),
            '|', ('result', '=', False), ('result', '!=', 'skipped')
        ]
        duplicate_ids = self.search(cr, uid, domain, context=context)

        if len(duplicate_ids):
            extra_info.update({'state': 'duplicate', 'duplicate_id': duplicate_ids[0]})
            self.write(cr, uid, [duplicate_ids[0]], {'duplicate_id': build_id})
        self.write(cr, uid, [build_id], extra_info, context=context)
        return build_id

    def reset(self, cr, uid, ids, context=None):
        self.write(cr, uid, ids, { 'state' : 'pending' }, context=context)

    def logger(self, cr, uid, ids, *l, **kw):
        l = list(l)
        for build in self.browse(cr, uid, ids, **kw):
            l[0] = "%s %s" % (build.dest , l[0])
            _logger.debug(*l)

    def list_jobs(self):
        return sorted(job for job in dir(self) if _re_job.match(job))

    def find_port(self, cr, uid):
        # currently used port
        ids = self.search(cr, uid, [('state','not in',['pending','done'])])
        ports = set(i['port'] for i in self.read(cr, uid, ids, ['port']))

        # starting port
        icp = self.pool['ir.config_parameter']
        port = int(icp.get_param(cr, uid, 'runbot.starting_port', default=2000))

        # find next free port
        while port in ports:
            port += 2

        return port

    def get_closest_branch_name(self, cr, uid, ids, target_repo_id, hint_branches, context=None):
        """Return the name of the closest common branch between both repos
        Rules priority for choosing the branch from the other repo is:
        1. Same branch name
        2. Common ancestors (git merge-base)
        3. Name splitting on '-' character
        Note that PR numbers are replaced by the branch name from which the PR is made,
        to prevent the above rules to mistakenly link PR of different repos together.
        """
        branch_pool = self.pool['runbot.branch']
        for build in self.browse(cr, uid, ids, context=context):
            branch, repo = build.branch_id, build.repo_id
            name = branch.branch_name
            # Use github API to find name of branch on which the PR is made
            if repo.token and name.startswith('refs/pull/'):
                pull_number = name[len('refs/pull/'):]
                pr = repo.github('/repos/:owner/:repo/pulls/%s' % pull_number)
                name = 'refs/heads/' + pr['base']['ref']
            # Find common branch names between repo and target repo
            branch_ids = branch_pool.search(cr, uid, [('repo_id.id', '=', repo.id)])
            target_ids = branch_pool.search(cr, uid, [('repo_id.id', '=', target_repo_id)])
            branch_names = branch_pool.read(cr, uid, branch_ids, ['branch_name', 'name'], context=context)
            target_names = branch_pool.read(cr, uid, target_ids, ['branch_name', 'name'], context=context)
            possible_repo_branches = set([i['branch_name'] for i in branch_names if i['name'].startswith('refs/heads')])
            possible_target_branches = set([i['branch_name'] for i in target_names if i['name'].startswith('refs/heads')])
            possible_branches = possible_repo_branches.intersection(possible_target_branches)
            if name not in possible_branches:
                hinted_branches = possible_branches.intersection(hint_branches)
                if hinted_branches:
                    possible_branches = hinted_branches
                common_refs = {}
                for target_branch_name in possible_branches:
                    try:
                        commit = repo.git(['merge-base', branch.name, target_branch_name]).strip()
                        cmd = ['log', '-1', '--format=%cd', '--date=iso', commit]
                        common_refs[target_branch_name] = repo.git(cmd).strip()
                    except subprocess.CalledProcessError:
                        # If merge-base doesn't find any common ancestor, the command exits with a
                        # non-zero return code, resulting in subprocess.check_output raising this
                        # exception. We ignore this branch as there is no common ref between us.
                        continue
                if common_refs:
                    name = sorted(common_refs.iteritems(), key=operator.itemgetter(1), reverse=True)[0][0]
                else:
                    # If all else as failed, fallback on '-' splitting
                    name = build.branch_id.name.split('-', 1)[0]
            return name

    def path(self, cr, uid, ids, *l, **kw):
        for build in self.browse(cr, uid, ids, context=None):
            root = self.pool['runbot.repo'].root(cr, uid)
            return os.path.join(root, 'build', build.dest, *l)

    def server(self, cr, uid, ids, *l, **kw):
        for build in self.browse(cr, uid, ids, context=None):
            if os.path.exists(build.path('odoo')):
                return build.path('odoo', *l)
            return build.path('openerp', *l)

    def checkout(self, cr, uid, ids, context=None):
        branch_pool = self.pool.get('runbot.branch')
        for build in self.browse(cr, uid, ids, context=context):
            # starts from scratch
            if os.path.isdir(build.path()):
                shutil.rmtree(build.path())

            # runbot log path
            mkdirs([build.path("logs"), build.server('addons')])

            # checkout branch
            principal_branch_version = 'pull' in build.name and build.branch_id.branch_name or build.name
            #export with last version when is a pull request build
            build.branch_id.repo_id.git_export(principal_branch_version, build.path())

            # TODO use git log to get commit message date and author

            # v6 rename bin -> openerp
            if os.path.isdir(build.path('bin/addons')):
                shutil.move(build.path('bin'), build.server())

            # fallback for addons-only community/project branches
            additional_modules = []
            if not os.path.isfile(build.server('__init__.py')):
                # Use modules to test previously configured in the repository
                modules_to_test = build.modules
                if not modules_to_test:
                    # Find modules to test from the folder branch
                    modules_to_test = ','.join(
                        os.path.basename(os.path.dirname(a))
                        for a in glob.glob(build.path('*/__openerp__.py'))
                    )
                build.write({'modules': modules_to_test})
                hint_branches = set()

                #Find branch from repository dependencies with same name of principal branch (odoo version)
                repo_depend_ids = [repo_depend.id for repo_depend in build.repo_id.dependency_ids]
                branch_depends_ids = branch_pool.search(cr, uid, [('branch_name', '=', build.branch_id.branch_name), ('repo_id', 'in', repo_depend_ids)], context=context)

                pr_original_branch_id = build.branch_dependency_id and build.branch_dependency_id.branch_base_id and build.branch_dependency_id.branch_base_id.id or False
                try:
                    pr_original_branch_index = branch_depends_ids.index( pr_original_branch_id )
                except ValueError:
                    pr_original_branch_index = False
                if pr_original_branch_index is not False:
                    #Replace original branch with PR
                    branch_depends_ids[ pr_original_branch_index ] = build.branch_dependency_id.id

                #Make environment
                for branch_depend in branch_pool.browse(cr, uid, branch_depends_ids, context=context):
                    branch_depend.repo_id.git_export(branch_depend.name, build.path())

                # Finally mark all addons to move to openerp/addons
                additional_modules += [
                    os.path.dirname(module)
                    for module in glob.glob(build.path('*/__openerp__.py'))
                ]

            # move all addons to server addons path
            for module in set(glob.glob(build.path('addons/*')) + additional_modules):
                basename = os.path.basename(module)
                if not os.path.exists(build.server('addons', basename)):
                    shutil.move(module, build.server('addons'))
                else:
                    build._log(
                        'Building environment',
                        'You have duplicate modules in your branches "%s"' % basename
                    )

    def pg_dropdb(self, cr, uid, dbname):
        #run(['dropdb', dbname])
        try:
            exp_drop(dbname)
        except Exception, e:
            pass
        # cleanup filestore
        datadir = appdirs.user_data_dir()
        paths = [os.path.join(datadir, pn, 'filestore', dbname) for pn in 'OpenERP Odoo'.split()]
        run(['rm', '-rf'] + paths)

    def pg_createdb(self, cr, uid, dbname):
        self.pg_dropdb(cr, uid, dbname)
        _logger.debug("createdb %s", dbname)
        #run(['createdb', '--encoding=unicode', '--lc-collate=C', '--template=template0', dbname])
        _create_empty_database(dbname)

    def cmd(self, cr, uid, ids, context=None):
        """Return a list describing the command to start the build"""
        for build in self.browse(cr, uid, ids, context=context):
            # Server
            server_path = build.path("openerp-server")
            # for 7.0
            if not os.path.isfile(server_path):
                server_path = build.path("openerp-server.py")
            # for 6.0 branches
            if not os.path.isfile(server_path):
                server_path = build.path("bin/openerp-server.py")

            # modules
            if build.modules:
                modules = build.modules
            else:
                l = glob.glob(build.server('addons', '*', '__init__.py'))
                modules = set(os.path.basename(os.path.dirname(i)) for i in l)
                modules = modules - set(['auth_ldap', 'document_ftp', 'hw_escpos', 'hw_proxy', 'hw_scanner', 'base_gengo', 'website_gengo', 'crm_partner_assign', 'l10n_in_hr_payroll', 'account_followup'])
                modules = ",".join(list(modules))

            # commandline
            cmd = [
                sys.executable,
                server_path,
                "--xmlrpc-port=%d" % build.port,
                "--addons-path=%s" % build.server('addons'),
                #"--without-demo=False",
                #"-r %s" % config['db_user'],
                #"-w %s" % config['db_password'] ,
                #"--db_host=%s" % config['db_host'],
                #"--db_port=%s" % config['db_port'],
            ]
            if config['db_user'] and config['db_user'] != 'False':
                import getpass
		if config['db_user'] != getpass.getuser():
		    cmd.extend(["-r", config['db_user']])
	    if config['db_password'] and config['db_password'] != 'False':
                cmd.extend(["-w", config['db_password']])
	    if config['db_host'] and config['db_host'] != 'False':
                cmd.append("--db_host=%s" % config['db_host'])
            if config['db_port'] and config['db_port'] != 'False':
                cmd.append("--db_port=%s" % config['db_port'])
            # options
            if grep(build.server("tools/config.py"), "no-xmlrpcs"):
                cmd.append("--no-xmlrpcs")
            if grep(build.server("tools/config.py"), "no-netrpc"):
                cmd.append("--no-netrpc")
            if grep(build.server("tools/config.py"), "log-db"):
                logdb = cr.dbname
                if config['db_host'] and grep(build.server('sql_db.py'), 'allow_uri'):
                    logdb = 'postgres://{cfg[db_user]}:{cfg[db_password]}@{cfg[db_host]}/{db}'.format(cfg=config, db=cr.dbname)
                cmd += ["--log-db=%s" % logdb]
            if grep(build.server("tools/config.py"), "max-cron-threads"):
                cmd += ["--max-cron-threads=0"]

            if grep(build.server("tools/config.py"), "data-dir"):
                datadir = build.path('datadir')
                if not os.path.exists(datadir):
                    os.mkdir(datadir)
                cmd += ["--data-dir", datadir]

        # coverage
        #coverage_file_path=os.path.join(log_path,'coverage.pickle')
        #coverage_base_path=os.path.join(log_path,'coverage-base')
        #coverage_all_path=os.path.join(log_path,'coverage-all')
        #cmd = ["coverage","run","--branch"] + cmd
        #self.run_log(cmd, logfile=self.test_all_path)
        #run(["coverage","html","-d",self.coverage_base_path,"--ignore-errors","--include=*.py"],env={'COVERAGE_FILE': self.coverage_file_path})

        return cmd, modules

    def spawn(self, cmd, lock_path, log_path, cpu_limit=None, shell=False):
        def preexec_fn():
            os.setsid()
            if cpu_limit:
                # set soft cpulimit
                soft, hard = resource.getrlimit(resource.RLIMIT_CPU)
                r = resource.getrusage(resource.RUSAGE_SELF)
                cpu_time = r.ru_utime + r.ru_stime
                resource.setrlimit(resource.RLIMIT_CPU, (cpu_time + cpu_limit, hard))
            # close parent files
            os.closerange(3, os.sysconf("SC_OPEN_MAX"))
            lock(lock_path)
        out=open(log_path,"w")
        out.write("\ncmd: %s\n" % cmd)
        _logger.debug("spawn: %s stdout: %s", ' '.join(cmd), log_path)
        p=subprocess.Popen(cmd, stdout=out, stderr=out, preexec_fn=preexec_fn, shell=shell)
        return p.pid

    def github_status(self, cr, uid, ids, context=None):
        """Notify github of failed/successful builds"""
        runbot_domain = self.pool['runbot.repo'].domain(cr, uid)
        for build in self.browse(cr, uid, ids, context=context):
            continue  # Temp to force use from inherit method of prebuild
            if build.repo_id.host_driver != 'github':
                raise Exception('Repository does not have a driver to use github')
            desc = "runbot build %s" % (build.dest,)
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
                "context": "ci/runbot"
            }
            try:
                build.repo_id.github('/repos/:owner/:repo/statuses/%s' % build.name, status)
                _logger.debug("github status %s update to %s", build.name, state)
            except Exception:
                _logger.exception("github status error")

    """
    def job_00_init(self, cr, uid, build, lock_path, log_path):
        build._log('init', 'Init build environment')
        # notify pending build - avoid confusing users by saying nothing
        if build.repo_id.host_driver == 'github':
            build.github_status()
        build.checkout()
        return -2

    def job_10_test_base(self, cr, uid, build, lock_path, log_path):
        build._log('test_base', 'Start test base module')
        # run base test
        self.pg_createdb(cr, uid, "%s-base" % build.dest)
        cmd, mods = build.cmd()
        if grep(build.server("tools/config.py"), "test-enable"):
            cmd.append("--test-enable")
        cmd += ['-d', '%s-base' % build.dest, '-i', 'base', '--stop-after-init', '--log-level=test', '--max-cron-threads=0']
        return self.spawn(cmd, lock_path, log_path, cpu_limit=300)
    """
    def job_20_test_all(self, cr, uid, build, lock_path, log_path):
        build._log('test_all', 'Start test all modules')
        if build.repo_id.host_driver == 'github':
            build.github_status()
        # checkout source
        build.checkout()
        # run base test
        self.pg_createdb(cr, uid, "%s-all" % build.dest)
        cmd, mods = build.cmd()
        if grep(build.server("tools/config.py"), "test-enable"):
            cmd.append("--test-enable")
        cmd += ['-d', '%s-all' % build.dest, '-i', mods, '--stop-after-init', '--log-level=test']
        # reset job_start to an accurate job_20 job_time
        build.write({'job_start': now()})
        return self.spawn(cmd, lock_path, log_path, cpu_limit=2100)

    def job_30_run(self, cr, uid, build, lock_path, log_path):
        # adjust job_end to record an accurate job_20 job_time
        build._log('run', 'Start running build %s' % build.dest)
        log_all = build.path('logs', 'job_20_test_all.txt')
        if not os.path.isfile(log_all):
            if not os.path.isdir(os.path.dirname(log_all)):
                os.makedirs(os.path.dirname(log_all))
            open(log_all, "w").write("created manually :( because is unexists.")
        log_time = time.localtime(os.path.getmtime(log_all))
        v = {
            'job_end': time.strftime(openerp.tools.DEFAULT_SERVER_DATETIME_FORMAT, log_time),
        }
        if grep(log_all, ".modules.loading: Modules loaded."):
            if rfind(log_all, _re_error,
                     excludes_wtraceback=[
                        # We have this false error into 8.0 with new server. TODO: Fix this error
                        "openerp.addons.base.ir.ir_mail_server: Mail delivery failed via SMTP server 'localhost'.",
                        "openerp.addons.mail.mail_mail: failed sending mail.mail",
                     ]):
                v['result'] = "ko"
            elif rfind(log_all, _re_warning, excludes=["no translation for language", "Unable to get information for locale"]):
                v['result'] = "warn"
            elif not grep(build.server("test/common.py"), "post_install") or grep(log_all, "Initiating shutdown."):
                v['result'] = "ok"
        else:
            v['result'] = "ko"
        build.write(v)
        if build.repo_id.host_driver == 'github':
            build.github_status()

        # run server
        cmd, mods = build.cmd()
        if os.path.exists(build.server('addons/im_livechat')):
            cmd += ["--workers", "2"]
            cmd += ["--longpolling-port", "%d" % (build.port + 1)]
            try:
                cmd.remove("--max-cron-threads=0")
                cmd += ["--max-cron-threads=1"]
            except ValueError:
                pass

        cmd += ['-d', "%s-all" % build.dest]

        if grep(build.server("tools/config.py"), "db-filter"):
            if build.repo_id.nginx:
                cmd += ['--db-filter','%d.*$']
            else:
                cmd += ['--db-filter','%s.*$' % build.dest]

        ## Web60
        #self.client_web_path=os.path.join(self.running_path,"client-web")
        #self.client_web_bin_path=os.path.join(self.client_web_path,"openerp-web.py")
        #self.client_web_doc_path=os.path.join(self.client_web_path,"doc")
        #webclient_config % (self.client_web_port+port,self.server_net_port+port,self.server_net_port+port)
        #cfgs = [os.path.join(self.client_web_path,"doc","openerp-web.cfg"), os.path.join(self.client_web_path,"openerp-web.cfg")]
        #for i in cfgs:
        #    f=open(i,"w")
        #    f.write(config)
        #    f.close()
        #cmd=[self.client_web_bin_path]

        return self.spawn(cmd, lock_path, log_path, cpu_limit=None)

    def force(self, cr, uid, ids, context=None):
        """Force a rebuild"""
        for build in self.browse(cr, uid, ids, context=context):
            domain = [('state', '=', 'pending')]
            pending_ids = self.search(cr, uid, domain, order='id', limit=1)
            if len(pending_ids):
                sequence = pending_ids[0]
            else:
                sequence = self.search(cr, uid, [], order='id desc', limit=1)[0]

            # Force it now
            if build.state == 'done' and build.result == 'skipped':
                values = {'state': 'pending', 'sequence':sequence, 'result': ''}
                self.write(cr, SUPERUSER_ID, [build.id], values, context=context)
            # or duplicate it
            elif build.state in ['running', 'done', 'duplicate']:
                self.copy(cr, 1, build.id, {}, context=context)
            return build.repo_id.id

    def schedule(self, cr, uid, ids, context=None):
        jobs = self.list_jobs()
        icp = self.pool['ir.config_parameter']
        timeout = int(icp.get_param(cr, uid, 'runbot.timeout', default=1800))

        for build in self.browse(cr, uid, ids, context=context):
            if build.state == 'pending':
                # allocate port and schedule first job
                port = self.find_port(cr, uid)
                values = {
                    'host': fqdn(),
                    'port': port,
                    'state': 'testing',
                    'job': jobs[0],
                    'job_start': now(),
                    'job_end': False,
                }
                build.write(values)
                cr.commit()
            else:
                # check if current job is finished
                lock_path = build.path('logs', '%s.lock' % build.job)
                if locked(lock_path):
                    # kill if overpassed
                    if build.job != jobs[-1] and build.job_time > timeout:
                        build.logger('%s time exceded (%ss)', build.job, build.job_time)
                        build.kill(result='killed')
                    continue
                build.logger('%s finished', build.job)
                # schedule
                v = {}
                # testing -> running
                if build.job == jobs[-2]:
                    v['state'] = 'running'
                    v['job'] = jobs[-1]
                    v['job_end'] = now(),
                # running -> done
                elif build.job == jobs[-1]:
                    v['state'] = 'done'
                    v['job'] = ''
                # testing
                else:
                    v['job'] = jobs[jobs.index(build.job) + 1]
                build.write(v)
            build.refresh()

            # run job
            pid = None
            if build.state != 'done':
                build.logger('running %s', build.job)
                job_method = getattr(self,build.job)
                lock_path = build.path('logs', '%s.lock' % build.job)
                log_path = build.path('logs', '%s.txt' % build.job)
                pid = job_method(cr, uid, build, lock_path, log_path)
                build.write({'pid': pid})
            # needed to prevent losing pids if multiple jobs are started and one them raise an exception
            cr.commit()

            if pid == -2:
                # no process to wait, directly call next job
                # FIXME find a better way that this recursive call
                build.schedule()

            # cleanup only needed if it was not killed
            if build.state == 'done':
                build.cleanup()

    def skip(self, cr, uid, ids, context=None):
        self.write(cr, uid, ids, {'state': 'done', 'result': 'skipped'}, context=context)
        to_unduplicate = self.search(cr, uid, [('id', 'in', ids), ('duplicate_id', '!=', False)])
        if len(to_unduplicate):
            self.force(cr, uid, to_unduplicate, context=context)

    def cleanup(self, cr, uid, ids, context=None):
        for build in self.browse(cr, uid, ids, context=context):
            self.pg_dropdb(cr, uid, "%s-base" % build.dest)
            self.pg_dropdb(cr, uid, "%s-all" % build.dest)
            if os.path.isdir(build.path()):
                for item in os.listdir( build.path() ):
                    path_item = os.path.join(build.path(), item)
                    if os.path.isdir( path_item ):
                        if item != 'logs':
                            shutil.rmtree( path_item )
                    elif os.path.isfile( path_item ):
                        os.remove( path_item )

    def kill(self, cr, uid, ids, result=None, context=None):
        for build in self.browse(cr, uid, ids, context=context):
            build.logger('killing %s', build.pid)
            try:
                if not (build.pid==os.getpid() or build.pid==os.getppid() or build.pid==0):
                    os.killpg(build.pid, signal.SIGKILL)
            except OSError:
                pass
            v = {'state': 'done', 'job': False}
            if result:
                v['result'] = result
            build.write(v)
            cr.commit()
            if build.repo_id.host_driver == 'github' and build.repo_id.token:
                build.github_status()
            build.cleanup()

    def reap(self, cr, uid, ids):
        while True:
            try:
                pid, status, rusage = os.wait3(os.WNOHANG)
            except OSError:
                break
            if pid == 0:
                break
            _logger.debug('reaping: pid: %s status: %s', pid, status)

    def _log(self, cr, uid, ids, func, message, context=None):
        assert len(ids) == 1
        _logger.debug("Build %s %s %s", ids[0], func, message)
        self.pool['ir.logging'].create(cr, uid, {
            'build_id': ids[0],
            'level': 'INFO',
            'type': 'runbot',
            'name': 'odoo.runbot',
            'message': message,
            'path': 'runbot',
            'func': func,
            'line': '0',
        }, context=context)

class runbot_event(osv.osv):
    _inherit = 'ir.logging'
    _order = 'id'

    TYPES = [(t, t.capitalize()) for t in 'client server runbot'.split()]
    _columns = {
        'build_id': fields.many2one('runbot.build', 'Build'),
        'type': fields.selection(TYPES, string='Type', required=True, select=True),
    }

#----------------------------------------------------------
# Runbot Controller
#----------------------------------------------------------

class RunbotController(http.Controller):

    @http.route(['/runbot', '/runbot/repo/<model("runbot.repo"):repo>'], type='http', auth="public", website=True)
    def repo(self, repo=None, search='', limit='100', refresh='', **post):
        registry, cr, uid = request.registry, request.cr, request.uid

        branch_obj = registry['runbot.branch']
        build_obj = registry['runbot.build']
        icp = registry['ir.config_parameter']
        repo_obj = registry['runbot.repo']
        count = lambda dom: build_obj.search_count(cr, uid, dom)

        repo_ids = repo_obj.search(cr, request.uid, [], order='id', limit=1)# Needs just one runbot.repo in qweb view to work fine with prebuilds
        repo_ids += repo_obj.search(cr, request.uid, [('auto', '=', True)], order='id')# Show only repo with auto=True (works without prebuild)
        #repo_ids = repo_obj.search(cr, uid, [])
        repos = repo_obj.browse(cr, uid, repo_ids)
        if not repo and repos:
            repo = repos[0]

        context = {
            'repos': repos,
            'repo': repo,
            'host_stats': [],
            'pending_total': count([('state','=','pending')]),
            'limit': limit,
            'search': search,
            'refresh': refresh,
        }

        if repo:
            filters = {key: post.get(key, '1') for key in ['pending', 'testing', 'running', 'done']}
            domain = [('repo_id','=',repo.id)]
            domain += [('state', '!=', key) for key, value in filters.iteritems() if value == '0']
            if search:
                domain += ['|', '|', ('dest', 'ilike', search), ('subject', 'ilike', search), ('branch_id.branch_name', 'ilike', search)]

            build_ids = build_obj.search(cr, uid, domain, limit=int(limit))
            branch_ids, build_by_branch_ids = [], {}

            if build_ids:
                branch_query = """
                    SELECT br.id AS branch_id, 
                        bu.branch_dependency_id,
                        CASE WHEN br.sticky AND bu.branch_dependency_id IS NULL THEN True
                             ELSE False
                        END AS real_sticky
                    FROM runbot_branch br 
                    INNER JOIN runbot_build bu 
                       ON br.id=bu.branch_id 
                    WHERE bu.id in %s
                    ORDER BY real_sticky DESC, bu.sequence DESC--, br.id DESC, bu.branch_dependency_id DESC
                """
                #sticky_dom = [('repo_id','=',repo.id), ('sticky', '=', True)]
                #sticky_branch_ids = [] if search else branch_obj.search(cr, uid, sticky_dom)
                cr.execute(branch_query, (tuple(build_ids),))
                branch_ids = uniq_list( [(br[0], br[1] or None) for br in cr.fetchall()] )

                build_query = """
                    SELECT
                        branch_id,
                        branch_dependency_id,
                        max(case when br_bu.row = 1 then br_bu.build_id end),
                        max(case when br_bu.row = 2 then br_bu.build_id end),
                        max(case when br_bu.row = 3 then br_bu.build_id end),
                        max(case when br_bu.row = 4 then br_bu.build_id end)
                    FROM (
                        SELECT
                            br.id AS branch_id,
                            bu.id AS build_id,
                            bu.branch_dependency_id AS branch_dependency_id,
                            row_number() OVER (PARTITION BY branch_id, bu.branch_dependency_id ORDER BY bu.id DESC) AS row
                        FROM
                            runbot_branch br INNER JOIN runbot_build bu ON br.id=bu.branch_id
                        WHERE bu.id in %s
                        GROUP BY br.id, branch_dependency_id, bu.id
                    ) AS br_bu
                    WHERE
                        row <= 4
                    GROUP BY br_bu.branch_id, br_bu.branch_dependency_id
                """
                cr.execute(build_query, (tuple(build_ids),))
                build_by_branch_ids = {
                    (rec[0], rec[1]): [r for r in rec[2:] if r is not None] for rec in cr.fetchall()
                }
            
            #branches = branch_obj.browse(cr, uid, branch_ids, context=request.context)
            build_ids = flatten(build_by_branch_ids.values())
            build_dict = {build.id: build for build in build_obj.browse(cr, uid, build_ids, context=request.context) }

            def branch_info(branch, branch_dependency):
                return {
                    'branch': branch,
                    'branch_dependency': branch_dependency,
                    'builds': [self.build_info(build_dict[build_id]) for build_id in build_by_branch_ids[branch.id, branch_dependency and branch_dependency.id or None]]
                }

            context.update({
                'branches': [ branch_info( 
                                branch_obj.browse(cr, uid, [branch_id], context=request.context)[0],\
                                branch_obj.browse(cr, uid, [branch_dependency_id], context=request.context)[0]\
                         ) \
                         for branch_id, branch_dependency_id in branch_ids ],
                'testing': count([('repo_id','=',repo.id), ('state','=','testing')]),
                'running': count([('repo_id','=',repo.id), ('state','=','running')]),
                'pending': count([('repo_id','=',repo.id), ('state','=','pending')]),
                'qu': QueryURL('/runbot/repo/'+slug(repo), search=search, limit=limit, refresh=refresh, **filters),
                'filters': filters,
            })

        for result in build_obj.read_group(cr, uid, [], ['host'], ['host']):
            if result['host']:
                context['host_stats'].append({
                    'host': result['host'],
                    'testing': count([('state', '=', 'testing'), ('host', '=', result['host'])]),
                    'running': count([('state', '=', 'running'), ('host', '=', result['host'])]),
                })

        return request.render("runbot.repo", context)

    def build_info(self, build):
        real_build = build.duplicate_id if build.state == 'duplicate' else build
        return {
            'id': build.id,
            'name': build.name,
            'state': real_build.state,
            'result': real_build.result,
            'subject': build.subject,
            'author': build.author,
            'dependency': build.branch_dependency_id and build.branch_dependency_id.name or False,
            'committer': build.committer,
            'dest': build.dest,
            'real_dest': real_build.dest,
            'job_age': s2human(real_build.job_age),
            'job_time': s2human(real_build.job_time),
            'job': real_build.job,
            'domain': real_build.domain,
            'host': real_build.host,
            'port': real_build.port,
            'subject': build.subject,
        }


    @http.route(['/runbot/build/<build_id>'], type='http', auth="public", website=True)
    def build(self, build_id=None, search=None, **post):
        registry, cr, uid, context = request.registry, request.cr, request.uid, request.context

        Build = registry['runbot.build']
        Logging = registry['ir.logging']

        build = Build.browse(cr, uid, [int(build_id)])[0]
        if not build.exists():
            return request.not_found()

        real_build = build.duplicate_id if build.state == 'duplicate' else build

        # other builds
        build_ids = Build.search(cr, uid, [('branch_id', '=', build.branch_id.id)])
        other_builds = Build.browse(cr, uid, build_ids)

        domain = ['|', ('dbname', '=like', '%s-%%' % real_build.dest), ('build_id', '=', real_build.id)]
        #if type:
        #    domain.append(('type', '=', type))
        #if level:
        #    domain.append(('level', '=', level))
        if search:
            domain.append(('name', 'ilike', search))
        logging_ids = Logging.search(cr, SUPERUSER_ID, domain)

        context = {
            'repo': build.repo_id,
            'build': self.build_info(build),
            'br': {'branch': build.branch_id},
            'logs': Logging.browse(cr, SUPERUSER_ID, logging_ids),
            'other_builds': other_builds
        }
        #context['type'] = type
        #context['level'] = level
        return request.render("runbot.build", context)

    @http.route(['/runbot/build/<build_id>/force'], type='http', auth="public", methods=['POST'])
    def build_force(self, build_id, **post):
        registry, cr, uid, context = request.registry, request.cr, request.uid, request.context
        repo_id = registry['runbot.build'].force(cr, uid, [int(build_id)])
        return werkzeug.utils.redirect('/runbot/repo/%s' % repo_id)

    @http.route([
        '/runbot/badge/<model("runbot.repo"):repo>/<branch>.svg',
        '/runbot/badge/<any(default,flat):theme>/<model("runbot.repo"):repo>/<branch>.svg',
    ], type="http", auth="public", methods=['GET', 'HEAD'])
    def badge(self, repo, branch, theme='default'):

        domain = [('repo_id', '=', repo.id),
                  ('branch_id.branch_name', '=', branch),
                  ('branch_id.sticky', '=', True),
                  ('state', 'in', ['testing', 'running', 'done']),
                  ('result', '!=', 'skipped'),
                  ]

        last_update = '__last_update'
        builds = request.registry['runbot.build'].search_read(
            request.cr, request.uid,
            domain, ['state', 'result', 'job_age', last_update],
            order='id desc', limit=1)

        if not builds:
            return request.not_found()

        build = builds[0]
        etag = request.httprequest.headers.get('If-None-Match')
        retag = hashlib.md5(build[last_update]).hexdigest()

        if etag == retag:
            return werkzeug.wrappers.Response(status=304)

        if build['state'] == 'testing':
            state = 'testing'
            cache_factor = 1
        else:
            cache_factor = 2
            if build['result'] == 'ok':
                state = 'success'
            elif build['result'] == 'warn':
                state = 'warning'
            else:
                state = 'failed'

        # from https://github.com/badges/shields/blob/master/colorscheme.json
        color = {
            'testing': "#dfb317",
            'success': "#4c1",
            'failed': "#e05d44",
            'warning': "#fe7d37",
        }[state]

        def text_width(s):
            fp = FontProperties(family='DejaVu Sans', size=11)
            w, h, d = TextToPath().get_text_width_height_descent(s, fp, False)
            return int(w + 1)

        class Text(object):
            __slot__ = ['text', 'color', 'width']
            def __init__(self, text, color):
                self.text = text
                self.color = color
                self.width = text_width(text) + 10

        data = {
            'left': Text(branch, '#555'),
            'right': Text(state, color),
        }
        five_minutes = 5 * 60
        headers = [
            ('Content-Type', 'image/svg+xml'),
            ('Cache-Control', 'max-age=%d' % (five_minutes * cache_factor,)),
            ('ETag', retag),
        ]
        return request.render("runbot.badge_" + theme, data, headers=headers)

    @http.route(['/runbot/b/<branch_name>', '/runbot/<model("runbot.repo"):repo>/<branch_name>'], type='http', auth="public", website=True)
    def fast_launch(self, branch_name=False, repo=False, **post):
        pool, cr, uid, context = request.registry, request.cr, request.uid, request.context
        Build = pool['runbot.build']

        domain = [('branch_id.branch_name', '=', branch_name)]

        if repo:
            domain.extend([('branch_id.repo_id', '=', repo.id)])
            order="sequence desc"
        else:
            order = 'repo_id ASC, sequence DESC'

        # Take the 10 lasts builds to find at least 1 running... Else no luck
        builds = Build.search(cr, uid, domain, order=order, limit=10, context=context)

        if builds:
            last_build = False
            for build in Build.browse(cr, uid, builds, context=context):
                if build.state == 'running' or (build.state == 'duplicate' and build.duplicate_id.state == 'running'):
                    last_build = build if build.state == 'running' else build.duplicate_id
                    break

            if not last_build:
                # Find the last build regardless the state to propose a rebuild
                last_build = Build.browse(cr, uid, builds[0], context=context)

            if last_build.state != 'running':
                url = "/runbot/build/%s?ask_rebuild=1" % last_build.id
            else:
                url = ("http://%s/login?db=%s-all&login=admin&key=admin%s" %
                       (last_build.domain, last_build.dest, "&redirect=/web?debug=1" if not build.branch_id.branch_name.startswith('7.0') else ''))
        else:
            return request.not_found()
        return werkzeug.utils.redirect(url)

# kill ` ps faux | grep ./static  | awk '{print $2}' `
# ps faux| grep Cron | grep -- '-all'  | awk '{print $2}' | xargs kill
# psql -l | grep " 000" | awk '{print $1}' | xargs -n1 dropdb

# - commit/pull more info
# - v6 support
# - host field in build
# - unlink build to remove ir_logging entires # ondelete=cascade
# - gc either build or only old ir_logging
# - if nginx server logfiles via each virtual server or map /runbot/static to root

# vim:
