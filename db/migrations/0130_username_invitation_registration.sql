-- migration:username_invitation_registration
ALTER TABLE identity_bindings DROP CONSTRAINT identity_bindings_identity_type_check;
ALTER TABLE identity_bindings ADD CONSTRAINT identity_bindings_identity_type_check
    CHECK (identity_type IN ('phone', 'username'));
