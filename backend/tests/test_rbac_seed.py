"""Run with python -m unittest backend.tests.test_rbac_seed."""

import unittest

from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from backend.app.db import models  # noqa: F401 - register User relationships
from backend.app.db.auth_models import ROLE_DEFAULTS, Permission, Role, ServiceAccount
from backend.app.db.identity_models import PermissionMaster, RoleMaster, RolePermissionMapping
from backend.app.services.rbac_service import role_permission_codes, sync_catalog


@compiles(JSONB, "sqlite")
def compile_jsonb_for_test(_type, _compiler, **_kwargs):
    return "JSON"


class CatalogSeedTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        for model in (PermissionMaster, RoleMaster, RolePermissionMapping, ServiceAccount):
            model.__table__.create(self.engine)
        self.db = Session(self.engine, autoflush=False)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_fresh_seed_and_repeat_have_complete_defaults(self):
        for _ in range(2):
            sync_catalog(self.db)
            for role in Role:
                self.assertEqual(
                    role_permission_codes(self.db, role.value),
                    {permission.value for permission in ROLE_DEFAULTS[role]},
                )

    def test_repeat_preserves_customised_bundle(self):
        sync_catalog(self.db)
        role = self.db.query(RoleMaster).filter_by(code=Role.ADMIN.value).one()
        permission = self.db.query(PermissionMaster).filter_by(code=Permission.CAMERAS_MANAGE.value).one()
        self.db.query(RolePermissionMapping).filter_by(
            role_id=role.id, permission_id=permission.id
        ).delete()
        self.db.commit()
        sync_catalog(self.db)
        self.assertNotIn(Permission.CAMERAS_MANAGE.value, role_permission_codes(self.db, role.code))


if __name__ == "__main__":
    unittest.main()
