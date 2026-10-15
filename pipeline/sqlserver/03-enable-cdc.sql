-- =============================================================================
--  CymbalGoalScouting: turn on change data capture for a DMS continuous job
--  Track A, Module 1 (aldb001).
-- =============================================================================
--  Runs AFTER the .bak is restored (gcloud sql import bak), as the `sqlserver`
--  login, connected to any database. Needs SQL Server Standard or Enterprise
--  (2017, 2019 or 2022): Cloud SQL does not offer CDC on Express or Web.
--
--  Database Migration Service reads the change tables CDC writes, so every
--  table it migrates continuously needs its own capture instance. Snapshot
--  isolation lets the full load read a consistent picture without blocking.
--
--  Source: https://docs.cloud.google.com/database-migration/docs/sqlserver-to-alloydb/configure-source-database
--  Idempotent: tables that already have a capture instance are skipped.
-- =============================================================================

IF NOT EXISTS (SELECT 1 FROM sys.databases WHERE name = N'CymbalGoalScouting' AND is_cdc_enabled = 1)
    EXEC msdb.dbo.gcloudsql_cdc_enable_db 'CymbalGoalScouting';
GO

ALTER DATABASE CymbalGoalScouting SET ALLOW_SNAPSHOT_ISOLATION ON;
GO

USE CymbalGoalScouting;
GO

DECLARE @table_name SYSNAME;
DECLARE cdc_tables CURSOR LOCAL FAST_FORWARD FOR
    SELECT t.name
    FROM sys.tables t
    WHERE t.schema_id = SCHEMA_ID(N'dbo')
      AND t.is_tracked_by_cdc = 0
      AND t.name IN (N'competitions', N'clubs', N'players', N'games', N'appearances',
                     N'game_events', N'player_valuations', N'transfers')
    ORDER BY t.name;

OPEN cdc_tables;
FETCH NEXT FROM cdc_tables INTO @table_name;
WHILE @@FETCH_STATUS = 0
BEGIN
    EXEC sys.sp_cdc_enable_table
        @source_schema = N'dbo',
        @source_name   = @table_name,
        @role_name     = NULL;
    FETCH NEXT FROM cdc_tables INTO @table_name;
END
CLOSE cdc_tables;
DEALLOCATE cdc_tables;
GO

-- What CDC now tracks: expect eight rows
SELECT t.name AS table_name, t.is_tracked_by_cdc
FROM sys.tables t
WHERE t.schema_id = SCHEMA_ID(N'dbo')
ORDER BY t.name;
GO
