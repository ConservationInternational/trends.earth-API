# Staging Script Import Fix

## Issue Summary

The staging deployment workflow was failing to copy scripts from the production database to staging. Investigation revealed that the script import logic in `setup_staging_environment.py` was incorrectly attempting to manipulate database sequences that don't exist for GUID-based tables.

## Root Cause

### Database Schema Analysis

The `script` table uses GUID (UUID) as its primary key:

```python
# From gefapi/models/script.py
class Script(db.Model):
    id = db.Column(
        db.GUID(),
        default=lambda: str(uuid.uuid4()),
        primary_key=True,
        autoincrement=False,  # ← No auto-increment!
    )
```

The initial migration confirms this:

```python
# From migrations/versions/9eb257aebbe6_.py
op.create_table(
    "script",
    sa.Column("id", GUID(), autoincrement=False, nullable=False),
    ...
)
```

### The Problem

The `copy_recent_scripts()` method was trying to:

1. **Delete all existing scripts**: `DELETE FROM script`
2. **Reset a non-existent sequence**: `SELECT setval('script_id_seq', 1, false)`
3. **Generate new sequential IDs** instead of preserving GUIDs

This approach had multiple issues:
- The `script_id_seq` sequence doesn't exist (GUIDs don't use sequences)
- Attempting to manipulate the non-existent sequence caused SQL errors
- New IDs were being generated instead of preserving production GUIDs
- Deleting scripts before import was unnecessary

## Solution

### Changes Made

1. **Removed unnecessary DELETE operation**
   - Removed: `staging_cursor.execute("DELETE FROM script")`
   - Scripts are now updated via `ON CONFLICT` clause

2. **Removed sequence manipulation**
   - Removed: `staging_cursor.execute("SELECT setval('script_id_seq', 1, false)")`
   - Removed: Post-import sequence reset logic
   - Reason: GUIDs don't use sequences

3. **Preserved original GUIDs from production**
   - Changed INSERT to include the `id` field
   - GUIDs are now preserved from production to staging

4. **Enhanced ON CONFLICT handling**
   - Added `id = EXCLUDED.id` to ON CONFLICT clause
   - Ensures GUID is updated even when slug conflicts

5. **Improved logging**
   - Added tracking for both new imports and updates
   - Better visibility into import process

### Code Changes

**Before (Incorrect):**
```python
# Clear existing scripts to avoid conflicts
staging_cursor.execute("DELETE FROM script")

# Reset the script ID sequence
staging_cursor.execute("SELECT setval('script_id_seq', 1, false)")

# Insert without ID field
staging_cursor.execute("""
    INSERT INTO script (name, slug, description, ...)
    VALUES (%s, %s, %s, ...)
    ON CONFLICT (slug) DO UPDATE SET ...
    RETURNING id
""", (...))

# Try to reset non-existent sequence
staging_cursor.execute(f"SELECT setval('script_id_seq', {next_val}, false)")
```

**After (Correct):**
```python
# No deletion needed - use ON CONFLICT to update

# Insert WITH ID field to preserve GUID
staging_cursor.execute("""
    INSERT INTO script (id, name, slug, description, ...)
    VALUES (%s, %s, %s, %s, ...)
    ON CONFLICT (slug) DO UPDATE SET
        id = EXCLUDED.id,
        name = EXCLUDED.name,
        ...
    RETURNING id, (xmax = 0) AS inserted
""", (script_id, ...))

# No sequence manipulation needed for GUIDs
```

## Testing

### Validation Tests

Created and ran validation tests to verify the fix:

1. **GUID Preservation Test**: ✅ PASS
   - Verified that GUIDs are preserved from production to staging
   - Confirmed no sequence manipulation is attempted

2. **Script Log Remapping Test**: ✅ PASS
   - Verified that script logs can be properly remapped using preserved GUIDs
   - Confirmed ID mapping works correctly

3. **ON CONFLICT Logic Test**: ✅ PASS
   - Verified that ON CONFLICT properly updates existing scripts
   - Confirmed new scripts are inserted correctly

All tests passed successfully.

## Impact

### What This Fixes

1. ✅ Scripts are now successfully copied from production to staging
2. ✅ Script GUIDs are preserved (important for consistency)
3. ✅ Build logs can be properly imported (they reference script GUIDs)
4. ✅ No SQL errors from non-existent sequence manipulation
5. ✅ Idempotent imports (can be run multiple times safely)

### What Was Not Changed

- Script logs (`script_log` table) - correctly uses integer IDs with sequences
- Status logs (`status_log` table) - correctly uses integer IDs with sequences
- User creation logic - already working correctly
- Overall staging deployment workflow

## Implementation Details

### Script Table Schema

```sql
CREATE TABLE script (
    id UUID PRIMARY KEY,           -- GUID, not integer!
    name VARCHAR(120) NOT NULL,
    slug VARCHAR(80) UNIQUE NOT NULL,
    description TEXT,
    created_at TIMESTAMP,
    updated_at TIMESTAMP,
    user_id UUID,
    status VARCHAR(80),
    ...
);
```

### Script Log Table Schema

```sql
CREATE TABLE script_log (
    id INTEGER PRIMARY KEY,        -- Integer with sequence
    text TEXT,
    register_date TIMESTAMP,
    script_id UUID REFERENCES script(id)
);
```

Note the difference: `script.id` is UUID, but `script_log.id` is INTEGER with a sequence.

## Best Practices Going Forward

1. **Always check table schema before manipulating sequences**
   - Not all tables use sequences
   - GUIDs/UUIDs don't need sequences

2. **Use ON CONFLICT for idempotent operations**
   - Safer than DELETE + INSERT
   - Allows for re-running imports

3. **Preserve identifiers when copying between environments**
   - Makes troubleshooting easier
   - Maintains referential integrity

4. **Add appropriate logging**
   - Track both inserts and updates
   - Log counts for verification

## Related Files

- `setup_staging_environment.py` - Main staging setup script
- `gefapi/models/script.py` - Script model definition
- `migrations/versions/9eb257aebbe6_.py` - Initial script table migration
- `.github/workflows/deploy-staging.yml` - Staging deployment workflow

## Testing in Staging

To verify this fix works in your staging environment:

1. Deploy to staging using the normal workflow
2. Check the migrate service logs for script import messages:
   ```bash
   docker service logs trends-earth-staging_migrate
   ```
3. Verify scripts were imported:
   ```sql
   SELECT COUNT(*) FROM script;
   SELECT COUNT(*) FROM script WHERE updated_at >= NOW() - INTERVAL '1 year';
   ```
4. Check that script logs were also imported:
   ```sql
   SELECT COUNT(*) FROM script_log;
   ```

## References

- Issue: Fix staging deploy scripts setup
- PR: [Link to PR]
- Related documentation: `docs/deployment/staging-database.md`
