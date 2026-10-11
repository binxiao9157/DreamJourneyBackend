"""Existing authenticated API business assertions with the new strategy enabled."""
from dataclasses import replace
from unittest.mock import patch
import app.main as main_module
import test_owner_truth_formal_memory_api as baseline

class TopicProfileAPITests(baseline.OwnerTruthFormalMemoryAPITests):
    def setUp(self):
        super().setUp()
        self.setting_patch=patch.object(main_module,'settings',replace(main_module.settings,owner_truth_biography_topics_enabled=True))
        self.setting_patch.start()
        self.addCleanup(self.setting_patch.stop)
