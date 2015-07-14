# -*- encoding: utf-8 -*-
##############################################################
#    Module Writen For Odoo, Open Source Management Solution
#
#    Copyright (c) 2011 Vauxoo - http://www.vauxoo.com
#    All Rights Reserved.
#    info Vauxoo (info@vauxoo.com)
#    coded by: moylop260@vauxoo.com
#    planned by: nhomar@vauxoo.com
#                moylop260@vauxoo.com
############################################################################

import logging

from openerp import api, models

_logger = logging.getLogger(__name__)


class Webhook(models.Model):
    _inherit = 'webhook'

    @api.one
    def update_branch(self, owner, repo, branch):
        """
        @owner: string with owner name of repo
        @repo: string with short name or repo
        @branch: string with name of branch or pr
                 to update
        """
        repos = self.env['runbot.repo'].search([
            ('owner', '=', owner),
            ('repo', '=', repo),
        ])
        if 'pull' in branch:
            ref_fetch = '+%s/head:%s' % (
                branch, branch
            )
        else:
            ref_fetch = '+%s:%s' % (branch, branch)
        repos.fetch_git([ref_fetch])
        branch_ids = repos.create_branches([branch])
        if not branch_ids:
            _logger.debug('Not branches updated from webhook')
            return branch_ids
        branch_id = branch_ids[0]
        branch_base_id = self.env['runbot.branch'].browse(
            branch_id).branch_base_id.id

        # Get prebuilds where 'branch_ids' has
        # 'check_new_commit' or 'check_new_pr'
        prebuild_line = self.env['runbot.prebuild.branch']
        pr_prebuild_datas = prebuild_line.search_read([
            ('prebuild_id.sticky', '=', True),
            ('check_pr', '=', True),
            ('branch_id', '=', branch_base_id),
        ], ['prebuild_id'])

        pr_prebuild_ids = [pr_prebuild_data['prebuild_id'][0]
                           for pr_prebuild_data in pr_prebuild_datas]
        self.env['runbot.prebuild'].browse(
            pr_prebuild_ids).create_build_pr()
        new_commit_prebuild_datas = prebuild_line.search_read([
            ('prebuild_id.sticky', '=', True),
            ('check_new_commit', '=', True),
            ('branch_id', '=', branch_id),
        ], ['prebuild_id'])
        new_commit_prebuild_ids = [new_commit_prebuild_data['prebuild_id'][
            0] for new_commit_prebuild_data in new_commit_prebuild_datas]
        self.env['runbot.prebuild'].browse(
            new_commit_prebuild_ids).create_prebuild_new_commit()

    @api.one
    def run_github_pull_request_prebuild(self):
        _logger.debug('runbot_prebuild:github_pr')
        owner_name = self.env.request.jsonrequest[
            'repository']['owner']['login']
        repo_name = self.env.request.jsonrequest[
            'repository']['name']
        ref = 'refs/pull/%s' % (
            self.env.request.jsonrequest['number'])
        self.update_branch(
            owner_name, repo_name, ref)

    @api.one
    def run_github_push_prebuild(self):
        _logger.debug('runbot_prebuild:github_push')
        owner_name = self.env.request.jsonrequest[
            'repository']['owner']['name']
        repo_name = self.env.request.jsonrequest[
            'repository']['name']
        ref = self.env.request.jsonrequest['ref']
        self.update_branch(
            owner_name, repo_name, ref)

    def run_github_status_prebuild(self):
        # TODO: To implement
        _logger.debug('runbot_prebuild:github_status')
