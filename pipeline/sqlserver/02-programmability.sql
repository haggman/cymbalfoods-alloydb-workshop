-- =============================================================================
--  CymbalGoalScouting: the legacy programmability layer
--  Track A, Module 1 (aldb001). Built by pipeline/05-sqlserver-package.ipynb.
-- =============================================================================
--  One scalar function, one view, one trigger, and seven stored procedures,
--  written the way a 2014 SQL Server team wrote them: cursors, temp tables,
--  table variables, TOP, ISNULL, + string building, FOR XML PATH, MERGE,
--  TRY/CATCH with THROW, explicit transactions, OUTPUT parameters.
--
--  THE DELIBERATE CONVERSION TRAP: usp_search_players.
--    The database uses the instance's default collation,
--    SQL_Latin1_General_CP1_CI_AS: case-INsensitive. A scout typing "messi"
--    finds "Lionel Messi" because LIKE ignores case here. PostgreSQL's LIKE is
--    case-sensitive, so a faithful line-by-line conversion compiles, applies,
--    passes row-count and hash validation, and silently returns nothing.
--
--  Things also worth watching in the conversion (not traps by design):
--    * usp_record_transfer: BEGIN TRANSACTION / COMMIT inside TRY/CATCH. In
--      PL/pgSQL a COMMIT inside a block with an EXCEPTION handler is an error at
--      run time, not at conversion time.
--    * usp_player_career_path: a cursor plus FOR XML PATH string aggregation.
--    * usp_upsert_valuation: MERGE ... OUTPUT $action into a table variable.
--    * fn_player_age: DATEDIFF(YEAR) counts year boundaries, not birthdays,
--      so the function corrects for it; the conversion has to keep that.
--    * MONEY, TINYINT, BIT and IDENTITY mappings; identity values after the
--      migration is promoted.
--    * trg_players_modified: an AFTER UPDATE trigger that stamps modified_at.
--      During CDC the replicated rows already carry the stamp, so a converted
--      trigger that also fires on the destination would stamp them again.
--
--  SQL Server 2014 compatible: each object is dropped with OBJECT_ID() and
--  re-created, because CREATE OR ALTER arrived in 2016 SP1.
-- =============================================================================

SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;
GO

-- =============================================================================
-- Scalar function: a player's age in whole years on a given date
-- =============================================================================
IF OBJECT_ID(N'dbo.fn_player_age', N'FN') IS NOT NULL
    DROP FUNCTION dbo.fn_player_age;
GO
CREATE FUNCTION dbo.fn_player_age
(
    @date_of_birth  DATE,
    @as_of          DATE
)
RETURNS INT
AS
BEGIN
    IF @date_of_birth IS NULL
        RETURN NULL;

    DECLARE @as_of_date DATE = ISNULL(@as_of, CAST(GETDATE() AS DATE));

    -- DATEDIFF(YEAR) counts calendar-year boundaries crossed, so a player born
    -- in December is a year "older" all year. Subtract one if the birthday has
    -- not come round yet.
    RETURN DATEDIFF(YEAR, @date_of_birth, @as_of_date)
         - CASE WHEN DATEADD(YEAR, DATEDIFF(YEAR, @date_of_birth, @as_of_date), @date_of_birth) > @as_of_date
                THEN 1 ELSE 0 END;
END
GO

-- =============================================================================
-- View: one row per player with the current club, league, age and value label
-- =============================================================================
IF OBJECT_ID(N'dbo.vw_player_profile', N'V') IS NOT NULL
    DROP VIEW dbo.vw_player_profile;
GO
CREATE VIEW dbo.vw_player_profile
AS
SELECT TOP 100 PERCENT
    p.player_id,
    p.player_name,
    p.player_name + N' (' + ISNULL(c.club_name, N'No current club') + N')' AS display_name,
    dbo.fn_player_age(p.date_of_birth, NULL)                               AS age,
    p.main_position,
    p.detailed_position,
    p.foot,
    p.height_in_cm,
    p.country_of_citizenship,
    c.club_name                                                             AS current_club_name,
    comp.competition_name                                                   AS current_league,
    p.market_value_in_eur,
    p.highest_market_value_in_eur,
    CASE WHEN p.market_value_in_eur IS NULL THEN N'unknown'
         ELSE N'EUR ' + CONVERT(NVARCHAR(20), CAST(p.market_value_in_eur / 1000000 AS DECIMAL(10,1))) + N'm'
    END                                                                     AS market_value_label,
    p.contract_expiration_date,
    p.is_active
FROM dbo.players p
LEFT JOIN dbo.clubs c           ON c.club_id = p.current_club_id
LEFT JOIN dbo.competitions comp ON comp.competition_id = c.domestic_competition_id
ORDER BY p.player_name;
GO

-- =============================================================================
-- Trigger: stamp players.modified_at on every update
-- =============================================================================
IF OBJECT_ID(N'dbo.trg_players_modified', N'TR') IS NOT NULL
    DROP TRIGGER dbo.trg_players_modified;
GO
CREATE TRIGGER dbo.trg_players_modified
ON dbo.players
AFTER UPDATE
AS
BEGIN
    SET NOCOUNT ON;

    IF NOT EXISTS (SELECT 1 FROM inserted)
        RETURN;

    -- RECURSIVE_TRIGGERS is OFF (the default), so this UPDATE does not re-fire.
    UPDATE p
    SET    modified_at = SYSUTCDATETIME()
    FROM   dbo.players p
    JOIN   inserted i ON i.player_id = p.player_id;
END
GO

-- =============================================================================
-- usp_search_players: the scouts' name search  (THE DELIBERATE TRAP)
--   EXEC dbo.usp_search_players @name_pattern = N'messi';
-- =============================================================================
IF OBJECT_ID(N'dbo.usp_search_players', N'P') IS NOT NULL
    DROP PROCEDURE dbo.usp_search_players;
GO
CREATE PROCEDURE dbo.usp_search_players
    @name_pattern   NVARCHAR(100),
    @position       NVARCHAR(30) = NULL,
    @max_rows       INT = 25
AS
BEGIN
    SET NOCOUNT ON;

    -- Scouts type part of a name in whatever case they like. The database
    -- collation is case-insensitive, so LIKE matches "messi" to "Lionel Messi".
    SELECT TOP (@max_rows)
        p.player_id,
        p.player_name + N' (' + ISNULL(c.club_name, N'No current club') + N')' AS display_name,
        p.main_position,
        dbo.fn_player_age(p.date_of_birth, NULL)                               AS age,
        ISNULL(p.market_value_in_eur, 0)                                        AS market_value_in_eur
    FROM dbo.players p
    LEFT JOIN dbo.clubs c ON c.club_id = p.current_club_id
    WHERE p.player_name LIKE N'%' + @name_pattern + N'%'
      AND (@position IS NULL OR p.main_position = @position)
    ORDER BY ISNULL(p.highest_market_value_in_eur, 0) DESC, p.player_id;
END
GO

-- =============================================================================
-- usp_get_player_season_stats: per-competition stats for one player-season
--   DECLARE @g INT, @m INT;
--   EXEC dbo.usp_get_player_season_stats 28003, 2014, @g OUTPUT, @m OUTPUT;
-- =============================================================================
IF OBJECT_ID(N'dbo.usp_get_player_season_stats', N'P') IS NOT NULL
    DROP PROCEDURE dbo.usp_get_player_season_stats;
GO
CREATE PROCEDURE dbo.usp_get_player_season_stats
    @player_id      INT,
    @season         INT,
    @total_goals    INT OUTPUT,
    @total_minutes  INT OUTPUT
AS
BEGIN
    SET NOCOUNT ON;

    CREATE TABLE #season_apps (
        competition_id      NVARCHAR(10)    NULL,
        competition_name    NVARCHAR(100)   NULL,
        apps                INT             NOT NULL,
        goals               INT             NOT NULL,
        assists             INT             NOT NULL,
        minutes_played      INT             NOT NULL,
        yellow_cards        INT             NOT NULL,
        red_cards           INT             NOT NULL
    );

    INSERT INTO #season_apps
        (competition_id, competition_name, apps, goals, assists, minutes_played, yellow_cards, red_cards)
    SELECT a.competition_id,
           comp.competition_name,
           COUNT(*),
           SUM(a.goals),
           SUM(a.assists),
           SUM(a.minutes_played),
           SUM(a.yellow_cards),
           SUM(a.red_cards)
    FROM dbo.appearances a
    JOIN dbo.games g                ON g.game_id = a.game_id
    LEFT JOIN dbo.competitions comp ON comp.competition_id = a.competition_id
    WHERE a.player_id = @player_id
      AND g.season = @season
    GROUP BY a.competition_id, comp.competition_name;

    SELECT @total_goals   = ISNULL(SUM(goals), 0),
           @total_minutes = ISNULL(SUM(minutes_played), 0)
    FROM #season_apps;

    SELECT competition_id,
           competition_name,
           apps,
           goals,
           assists,
           minutes_played,
           CASE WHEN minutes_played > 0
                THEN CAST(goals * 90.0 / minutes_played AS DECIMAL(5,2))
                ELSE CAST(0 AS DECIMAL(5,2)) END AS goals_per_90,
           yellow_cards,
           red_cards
    FROM #season_apps
    ORDER BY minutes_played DESC, competition_id;

    DROP TABLE #season_apps;
END
GO

-- =============================================================================
-- usp_club_season_table: the league table for one competition and season
--   EXEC dbo.usp_club_season_table N'GB1', 2023;
-- =============================================================================
IF OBJECT_ID(N'dbo.usp_club_season_table', N'P') IS NOT NULL
    DROP PROCEDURE dbo.usp_club_season_table;
GO
CREATE PROCEDURE dbo.usp_club_season_table
    @competition_id NVARCHAR(10),
    @season         INT
AS
BEGIN
    SET NOCOUNT ON;

    CREATE TABLE #results (
        club_id         INT NOT NULL,
        goals_for       INT NOT NULL,
        goals_against   INT NOT NULL,
        points          INT NOT NULL,
        won             INT NOT NULL,
        drawn           INT NOT NULL,
        lost            INT NOT NULL
    );

    -- Home results
    INSERT INTO #results (club_id, goals_for, goals_against, points, won, drawn, lost)
    SELECT home_club_id,
           home_club_goals,
           away_club_goals,
           CASE WHEN home_club_goals > away_club_goals THEN 3
                WHEN home_club_goals = away_club_goals THEN 1 ELSE 0 END,
           CASE WHEN home_club_goals > away_club_goals THEN 1 ELSE 0 END,
           CASE WHEN home_club_goals = away_club_goals THEN 1 ELSE 0 END,
           CASE WHEN home_club_goals < away_club_goals THEN 1 ELSE 0 END
    FROM dbo.games
    WHERE competition_id = @competition_id
      AND season = @season
      AND home_club_id IS NOT NULL
      AND home_club_goals IS NOT NULL
      AND away_club_goals IS NOT NULL;

    -- Away results
    INSERT INTO #results (club_id, goals_for, goals_against, points, won, drawn, lost)
    SELECT away_club_id,
           away_club_goals,
           home_club_goals,
           CASE WHEN away_club_goals > home_club_goals THEN 3
                WHEN away_club_goals = home_club_goals THEN 1 ELSE 0 END,
           CASE WHEN away_club_goals > home_club_goals THEN 1 ELSE 0 END,
           CASE WHEN away_club_goals = home_club_goals THEN 1 ELSE 0 END,
           CASE WHEN away_club_goals < home_club_goals THEN 1 ELSE 0 END
    FROM dbo.games
    WHERE competition_id = @competition_id
      AND season = @season
      AND away_club_id IS NOT NULL
      AND home_club_goals IS NOT NULL
      AND away_club_goals IS NOT NULL;

    SELECT ROW_NUMBER() OVER (ORDER BY SUM(r.points) DESC,
                                       SUM(r.goals_for) - SUM(r.goals_against) DESC,
                                       SUM(r.goals_for) DESC,
                                       r.club_id)                       AS position,
           c.club_name,
           COUNT(*)                                                     AS played,
           SUM(r.won)                                                   AS won,
           SUM(r.drawn)                                                 AS drawn,
           SUM(r.lost)                                                  AS lost,
           CAST(SUM(r.won) AS NVARCHAR(3)) + N'-'
             + CAST(SUM(r.drawn) AS NVARCHAR(3)) + N'-'
             + CAST(SUM(r.lost) AS NVARCHAR(3))                         AS record,
           SUM(r.goals_for)                                             AS goals_for,
           SUM(r.goals_against)                                         AS goals_against,
           SUM(r.goals_for) - SUM(r.goals_against)                      AS goal_difference,
           SUM(r.points)                                                AS points
    FROM #results r
    JOIN dbo.clubs c ON c.club_id = r.club_id
    GROUP BY r.club_id, c.club_name
    ORDER BY position;

    DROP TABLE #results;
END
GO

-- =============================================================================
-- usp_top_scorers: the scoring chart, ties included
--   EXEC dbo.usp_top_scorers N'GB1', 2023, 10;
-- =============================================================================
IF OBJECT_ID(N'dbo.usp_top_scorers', N'P') IS NOT NULL
    DROP PROCEDURE dbo.usp_top_scorers;
GO
CREATE PROCEDURE dbo.usp_top_scorers
    @competition_id NVARCHAR(10),
    @season         INT,
    @top_n          INT = 10
AS
BEGIN
    SET NOCOUNT ON;

    SELECT TOP (@top_n) WITH TIES
        p.player_id,
        p.player_name,
        ISNULL(MAX(c.club_name), N'Unknown')   AS club_name,
        SUM(a.goals)                           AS goals,
        SUM(a.assists)                         AS assists,
        SUM(a.minutes_played)                  AS minutes_played
    FROM dbo.appearances a
    JOIN dbo.games g      ON g.game_id = a.game_id
    JOIN dbo.players p    ON p.player_id = a.player_id
    LEFT JOIN dbo.clubs c ON c.club_id = a.player_club_id
    WHERE g.competition_id = @competition_id
      AND g.season = @season
    GROUP BY p.player_id, p.player_name
    ORDER BY SUM(a.goals) DESC;
END
GO

-- =============================================================================
-- usp_player_career_path: a player's moves, built row by row with a cursor
--   DECLARE @path NVARCHAR(4000);
--   EXEC dbo.usp_player_career_path 28003, @path OUTPUT;
-- =============================================================================
IF OBJECT_ID(N'dbo.usp_player_career_path', N'P') IS NOT NULL
    DROP PROCEDURE dbo.usp_player_career_path;
GO
CREATE PROCEDURE dbo.usp_player_career_path
    @player_id      INT,
    @career_path    NVARCHAR(4000) OUTPUT
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @to_club        NVARCHAR(100),
            @transfer_date  DATE,
            @fee            MONEY,
            @step           NVARCHAR(300);

    SET @career_path = NULL;

    DECLARE transfer_cursor CURSOR LOCAL FAST_FORWARD FOR
        SELECT ISNULL(t.to_club_name, N'Unknown club'),
               t.transfer_date,
               t.transfer_fee
        FROM dbo.transfers t
        WHERE t.player_id = @player_id
        ORDER BY t.transfer_date, t.transfer_id;

    OPEN transfer_cursor;
    FETCH NEXT FROM transfer_cursor INTO @to_club, @transfer_date, @fee;

    WHILE @@FETCH_STATUS = 0
    BEGIN
        SET @step = @to_club + N' (' + CONVERT(NVARCHAR(4), YEAR(@transfer_date))
                  + CASE WHEN @fee IS NULL THEN N''
                         WHEN @fee = 0     THEN N', free'
                         ELSE N', EUR ' + CONVERT(NVARCHAR(20), CAST(@fee / 1000000 AS DECIMAL(10,1))) + N'm'
                    END
                  + N')';

        SET @career_path = CASE WHEN @career_path IS NULL THEN @step
                                ELSE @career_path + N' > ' + @step END;

        FETCH NEXT FROM transfer_cursor INTO @to_club, @transfer_date, @fee;
    END

    CLOSE transfer_cursor;
    DEALLOCATE transfer_cursor;

    -- Distinct in-scope clubs, comma-separated: the pre-2017 FOR XML PATH idiom
    SELECT p.player_id,
           p.player_name,
           @career_path AS career_path,
           STUFF((SELECT N', ' + t.to_club_name
                  FROM dbo.transfers t
                  WHERE t.player_id = p.player_id
                    AND t.to_club_id IS NOT NULL
                  GROUP BY t.to_club_name
                  ORDER BY t.to_club_name
                  FOR XML PATH(''), TYPE).value('.', 'NVARCHAR(MAX)'), 1, 2, N'') AS in_scope_clubs
    FROM dbo.players p
    WHERE p.player_id = @player_id;
END
GO

-- =============================================================================
-- usp_upsert_valuation: record a market valuation (insert or update)
--   DECLARE @action NVARCHAR(10);
--   EXEC dbo.usp_upsert_valuation 28003, '2026-10-01', 20000000, NULL, @action OUTPUT;
-- =============================================================================
IF OBJECT_ID(N'dbo.usp_upsert_valuation', N'P') IS NOT NULL
    DROP PROCEDURE dbo.usp_upsert_valuation;
GO
CREATE PROCEDURE dbo.usp_upsert_valuation
    @player_id              INT,
    @valuation_date         DATE,
    @market_value_in_eur    MONEY,
    @current_club_id        INT = NULL,
    @action                 NVARCHAR(10) OUTPUT
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRY
        IF NOT EXISTS (SELECT 1 FROM dbo.players WHERE player_id = @player_id)
            RAISERROR(N'Player %d does not exist.', 16, 1, @player_id);

        DECLARE @merge_output TABLE (merge_action NVARCHAR(10));

        MERGE dbo.player_valuations AS target
        USING (SELECT @player_id            AS player_id,
                      @valuation_date       AS valuation_date,
                      @market_value_in_eur  AS market_value_in_eur,
                      ISNULL(@current_club_id,
                             (SELECT current_club_id FROM dbo.players WHERE player_id = @player_id)) AS current_club_id
              ) AS source
        ON  target.player_id = source.player_id
        AND target.valuation_date = source.valuation_date
        WHEN MATCHED THEN
            UPDATE SET market_value_in_eur = source.market_value_in_eur,
                       current_club_id     = source.current_club_id
        WHEN NOT MATCHED BY TARGET THEN
            INSERT (player_id, valuation_date, market_value_in_eur, current_club_id)
            VALUES (source.player_id, source.valuation_date, source.market_value_in_eur, source.current_club_id)
        OUTPUT $action INTO @merge_output;

        SELECT TOP 1 @action = merge_action FROM @merge_output;

        -- Keep the player's headline value current when this is the latest valuation
        UPDATE dbo.players
        SET    market_value_in_eur = @market_value_in_eur,
               highest_market_value_in_eur =
                   CASE WHEN ISNULL(highest_market_value_in_eur, 0) < @market_value_in_eur
                        THEN @market_value_in_eur
                        ELSE highest_market_value_in_eur END
        WHERE  player_id = @player_id
          AND  @valuation_date >= (SELECT MAX(valuation_date)
                                   FROM dbo.player_valuations
                                   WHERE player_id = @player_id);
    END TRY
    BEGIN CATCH
        SET @action = N'ERROR';
        THROW;
    END CATCH
END
GO

-- =============================================================================
-- usp_record_transfer: move a player to a new club, in one transaction
--   DECLARE @id INT;
--   EXEC dbo.usp_record_transfer 28003, 131, '2026-07-01', 0, @id OUTPUT;
-- =============================================================================
IF OBJECT_ID(N'dbo.usp_record_transfer', N'P') IS NOT NULL
    DROP PROCEDURE dbo.usp_record_transfer;
GO
CREATE PROCEDURE dbo.usp_record_transfer
    @player_id      INT,
    @to_club_id     INT,
    @transfer_date  DATE,
    @transfer_fee   MONEY = NULL,
    @transfer_id    INT OUTPUT
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    DECLARE @from_club_id   INT,
            @from_club_name NVARCHAR(100),
            @to_club_name   NVARCHAR(100),
            @market_value   MONEY,
            @season_start   INT;

    BEGIN TRY
        BEGIN TRANSACTION;

        SELECT @from_club_id   = p.current_club_id,
               @from_club_name = c.club_name,
               @market_value   = p.market_value_in_eur
        FROM dbo.players p
        LEFT JOIN dbo.clubs c ON c.club_id = p.current_club_id
        WHERE p.player_id = @player_id;

        IF @@ROWCOUNT = 0
            RAISERROR(N'Player %d does not exist.', 16, 1, @player_id);

        SELECT @to_club_name = club_name FROM dbo.clubs WHERE club_id = @to_club_id;

        IF @to_club_name IS NULL
            RAISERROR(N'Club %d does not exist.', 16, 1, @to_club_id);

        -- Seasons run July to June: a January move belongs to the season that began the July before
        SET @season_start = YEAR(@transfer_date) - CASE WHEN MONTH(@transfer_date) < 7 THEN 1 ELSE 0 END;

        INSERT INTO dbo.transfers
            (player_id, transfer_date, transfer_season, from_club_id, from_club_name,
             to_club_id, to_club_name, transfer_fee, market_value_in_eur)
        VALUES
            (@player_id, @transfer_date,
             RIGHT(CONVERT(NVARCHAR(4), @season_start), 2) + N'/' + RIGHT(CONVERT(NVARCHAR(4), @season_start + 1), 2),
             @from_club_id, @from_club_name, @to_club_id, @to_club_name, @transfer_fee, @market_value);

        SET @transfer_id = SCOPE_IDENTITY();

        UPDATE dbo.players
        SET    current_club_id = @to_club_id
        WHERE  player_id = @player_id;

        COMMIT TRANSACTION;
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0
            ROLLBACK TRANSACTION;
        THROW;
    END CATCH
END
GO
