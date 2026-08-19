-- migration:publication_share_grant_recipient_label
--
-- Keep only a server-derived, display-safe recipient label beside the
-- recipient subject hash. Raw phone numbers and account identifiers remain
-- outside the publication schema.

ALTER TABLE publication.share_grants
    ADD COLUMN grantee_display_label TEXT NOT NULL DEFAULT '已注册账户'
        CHECK (
            BTRIM(grantee_display_label) <> ''
            AND CHAR_LENGTH(grantee_display_label) <= 80
        );
