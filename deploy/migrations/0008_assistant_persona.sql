-- Per-tenant AI greeting: owner name + capability list (not hardcoded in code).

ALTER TABLE tenants
    ADD COLUMN IF NOT EXISTS assistant_owner TEXT NOT NULL DEFAULT '';

ALTER TABLE tenants
    ADD COLUMN IF NOT EXISTS assistant_capabilities TEXT[] NOT NULL
        DEFAULT ARRAY['查天气', '查路线', '查简历信息', '发邮件'];

UPDATE tenants
SET assistant_owner = CASE
        WHEN slug = 'yufan' THEN '张博洋'
        ELSE display_name
    END
WHERE assistant_owner = '';

UPDATE tenants
SET assistant_capabilities = ARRAY['查天气', '查路线', '查简历信息', '发邮件']
WHERE assistant_capabilities IS NULL
   OR cardinality(assistant_capabilities) = 0
   OR assistant_capabilities = ARRAY['查天气', '查酒店', '查路线', '查制度资料', '发邮件'];
