-- ─────────────────────────────────────────────────────────────────────────────
-- Platform setup 2/3: column masks referenced by the contracts.
--
-- A contract property tagged "pii:<type>" gets a column mask: the one named in its
-- `mask` customProperty, otherwise `default_pii_mask` from contractctl.yml.
-- Create the account group `pii_readers` (or change the group below) first.
-- Row filters referenced by a contract's `rowFilter` are created here as well.
-- ─────────────────────────────────────────────────────────────────────────────
USE CATALOG {{catalog}};

CREATE OR REPLACE FUNCTION governance.mask_pii(value STRING)
  RETURNS STRING
  COMMENT 'Default mask for personal data: hidden unless the caller is in pii_readers'
  RETURN CASE WHEN is_account_group_member('pii_readers') THEN value ELSE '***' END;

CREATE OR REPLACE FUNCTION governance.mask_email(email STRING)
  RETURNS STRING
  COMMENT 'Hides the local part of an e-mail address unless the caller is in pii_readers'
  RETURN CASE
    WHEN is_account_group_member('pii_readers') THEN email
    ELSE regexp_replace(email, '^[^@]+', '***')
  END;
