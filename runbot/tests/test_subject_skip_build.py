
import os
import shutil
import subprocess
import tempfile

from odoo.tests.common import TransactionCase


class TestRunbotSkipBuild(TransactionCase):
    def setUp(self):
        super().setUp()
        self.tmp_dir = tempfile.mkdtemp()
        self.work_tree = os.path.join(self.tmp_dir, "git_example")
        self.git_dir = os.path.join(self.work_tree, ".git")
        subprocess.call(["git", "init", self.git_dir])
        hooks_dir = os.path.join(self.git_dir, "hooks")
        if os.path.isdir(hooks_dir):
            # Avoid run a hook if a commit is created
            shutil.rmtree(hooks_dir)

    def tearDown(self):
        super().tearDown()
        shutil.rmtree(self.tmp_dir)

    def git(self, *cmd):
        subprocess.call([
            "git", "--git-dir=%s" % self.git_dir,
            "--work-tree=%s" % self.work_tree] + list(cmd))

    def test_subject_skip_build(self):
        """Test the [ci skip] feature"""
        self.git("commit", "--allow-empty", "-m", "Testing normal")
        repo = self.env["runbot.repo"].create({
            "name": self.git_dir,
        })
        import pdb;pdb.set_trace()
        repo._update_git()
