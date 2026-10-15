-- =============================================================================
--  CymbalGoalScouting: the legacy SQL Server database (schema)
--  Track A, Module 1 (aldb001). Built by pipeline/05-sqlserver-package.ipynb.
-- =============================================================================
--  CymbalGoal's scouting department has run on this database since about 2014.
--  It holds the same eight-table relational core as every other CymbalGoal lab,
--  written the way a SQL Server shop of that era would have written it:
--
--    * NVARCHAR(n) for text, DATE for dates, DATETIME2 for audit stamps
--    * MONEY for fees and market values
--    * BIT flags (clubs.is_big5, players.is_active)
--    * IDENTITY surrogate keys where the source had none worth keeping
--      (transfers.transfer_id, player_valuations.valuation_id)
--    * TINYINT match counters, clustered primary keys, nonclustered indexes
--
--  The data's real dirty edges are kept on purpose:
--    * transfers.from_club_id / to_club_id are NULL when the club is outside the
--      Big 5 + UEFA scope, while from_club_name / to_club_name still say who it was
--    * transfer_fee NULL means "fee unknown"; 0 means "free transfer"
--    * many players share a name (there are several Paulinhos)
--    * competition_type was normalized for UEFA competitions upstream
--
--  Run against database CymbalGoalScouting (or the build copy) with the
--  `sqlserver` login. The notebook applies NVARCHAR widths measured from the data;
--  the copy published next to the .bak is the exact script that ran.
--
--  SQL Server 2014 compatible: no CREATE OR ALTER, no DROP ... IF EXISTS.
--  Identifiers are snake_case so they match the other Track A labs.
-- =============================================================================

SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;
GO

-- ---------------------------------------------------------------------------
-- competitions
-- ---------------------------------------------------------------------------
CREATE TABLE dbo.competitions (
    competition_id          NVARCHAR(10)    NOT NULL,
    competition_code        NVARCHAR(100)   NULL,
    competition_name        NVARCHAR(100)   NOT NULL,
    competition_type        NVARCHAR(40)    NULL,
    competition_sub_type    NVARCHAR(60)    NULL,
    country_name            NVARCHAR(60)    NULL,
    domestic_league_code    NVARCHAR(10)    NULL,
    confederation           NVARCHAR(20)    NULL,
    total_clubs             INT             NULL,
    url                     NVARCHAR(400)   NULL,
    CONSTRAINT pk_competitions PRIMARY KEY CLUSTERED (competition_id)
);
GO

-- ---------------------------------------------------------------------------
-- clubs
-- ---------------------------------------------------------------------------
CREATE TABLE dbo.clubs (
    club_id                     INT             NOT NULL,
    club_code                   NVARCHAR(100)   NULL,
    club_name                   NVARCHAR(100)   NOT NULL,
    domestic_competition_id     NVARCHAR(10)    NULL,
    squad_size                  INT             NULL,
    average_age                 DECIMAL(4,1)    NULL,
    foreigners_number           INT             NULL,
    foreigners_percentage       DECIMAL(5,2)    NULL,
    national_team_players       INT             NULL,
    stadium_name                NVARCHAR(100)   NULL,
    stadium_seats               INT             NULL,
    net_transfer_record_eur     MONEY           NULL,
    coach_name                  NVARCHAR(100)   NULL,
    last_season                 SMALLINT        NULL,
    url                         NVARCHAR(400)   NULL,
    is_big5                     BIT             NOT NULL CONSTRAINT df_clubs_is_big5 DEFAULT (0),
    CONSTRAINT pk_clubs PRIMARY KEY CLUSTERED (club_id),
    CONSTRAINT fk_clubs_competition FOREIGN KEY (domestic_competition_id)
        REFERENCES dbo.competitions (competition_id)
);
GO

-- ---------------------------------------------------------------------------
-- players
-- ---------------------------------------------------------------------------
CREATE TABLE dbo.players (
    player_id                       INT             NOT NULL,
    player_name                     NVARCHAR(100)   NOT NULL,
    first_name                      NVARCHAR(100)   NULL,
    last_name                       NVARCHAR(100)   NULL,
    current_club_id                 INT             NULL,
    country_of_citizenship          NVARCHAR(60)    NULL,
    country_of_birth                NVARCHAR(60)    NULL,
    city_of_birth                   NVARCHAR(100)   NULL,
    date_of_birth                   DATE            NULL,
    main_position                   NVARCHAR(30)    NULL,
    detailed_position               NVARCHAR(40)    NULL,
    foot                            NVARCHAR(10)    NULL,
    height_in_cm                    SMALLINT        NULL,
    market_value_in_eur             MONEY           NULL,
    highest_market_value_in_eur     MONEY           NULL,
    contract_expiration_date        DATE            NULL,
    agent_name                      NVARCHAR(100)   NULL,
    international_caps              SMALLINT        NULL,
    international_goals             SMALLINT        NULL,
    last_season                     SMALLINT        NULL,
    url                             NVARCHAR(400)   NULL,
    is_active                       BIT             NOT NULL CONSTRAINT df_players_is_active DEFAULT (0),
    created_at                      DATETIME2(3)    NOT NULL CONSTRAINT df_players_created_at DEFAULT (SYSUTCDATETIME()),
    modified_at                     DATETIME2(3)    NULL,
    CONSTRAINT pk_players PRIMARY KEY CLUSTERED (player_id),
    CONSTRAINT fk_players_club FOREIGN KEY (current_club_id)
        REFERENCES dbo.clubs (club_id)
);
GO

-- ---------------------------------------------------------------------------
-- games
-- ---------------------------------------------------------------------------
CREATE TABLE dbo.games (
    game_id                 INT             NOT NULL,
    competition_id          NVARCHAR(10)    NOT NULL,
    season                  SMALLINT        NOT NULL,
    round_label             NVARCHAR(60)    NULL,
    game_date               DATE            NOT NULL,
    home_club_id            INT             NULL,
    away_club_id            INT             NULL,
    home_club_goals         TINYINT         NULL,
    away_club_goals         TINYINT         NULL,
    home_club_position      TINYINT         NULL,
    away_club_position      TINYINT         NULL,
    home_club_manager_name  NVARCHAR(100)   NULL,
    away_club_manager_name  NVARCHAR(100)   NULL,
    home_club_formation     NVARCHAR(40)    NULL,
    away_club_formation     NVARCHAR(40)    NULL,
    aggregate               NVARCHAR(10)    NULL,
    stadium                 NVARCHAR(100)   NULL,
    attendance              INT             NULL,
    referee                 NVARCHAR(100)   NULL,
    url                     NVARCHAR(400)   NULL,
    CONSTRAINT pk_games PRIMARY KEY CLUSTERED (game_id),
    CONSTRAINT fk_games_competition FOREIGN KEY (competition_id)
        REFERENCES dbo.competitions (competition_id),
    CONSTRAINT fk_games_home_club FOREIGN KEY (home_club_id)
        REFERENCES dbo.clubs (club_id),
    CONSTRAINT fk_games_away_club FOREIGN KEY (away_club_id)
        REFERENCES dbo.clubs (club_id)
);
GO

-- ---------------------------------------------------------------------------
-- appearances
-- ---------------------------------------------------------------------------
CREATE TABLE dbo.appearances (
    appearance_id       NVARCHAR(30)    NOT NULL,
    game_id             INT             NOT NULL,
    player_id           INT             NOT NULL,
    player_club_id      INT             NULL,
    competition_id      NVARCHAR(10)    NULL,
    appearance_date     DATE            NULL,
    yellow_cards        TINYINT         NOT NULL CONSTRAINT df_appearances_yellow DEFAULT (0),
    red_cards           TINYINT         NOT NULL CONSTRAINT df_appearances_red DEFAULT (0),
    goals               TINYINT         NOT NULL CONSTRAINT df_appearances_goals DEFAULT (0),
    assists             TINYINT         NOT NULL CONSTRAINT df_appearances_assists DEFAULT (0),
    minutes_played      SMALLINT        NOT NULL CONSTRAINT df_appearances_minutes DEFAULT (0),
    CONSTRAINT pk_appearances PRIMARY KEY CLUSTERED (appearance_id),
    CONSTRAINT fk_appearances_game FOREIGN KEY (game_id)
        REFERENCES dbo.games (game_id),
    CONSTRAINT fk_appearances_player FOREIGN KEY (player_id)
        REFERENCES dbo.players (player_id),
    CONSTRAINT fk_appearances_club FOREIGN KEY (player_club_id)
        REFERENCES dbo.clubs (club_id),
    CONSTRAINT fk_appearances_competition FOREIGN KEY (competition_id)
        REFERENCES dbo.competitions (competition_id)
);
GO

-- ---------------------------------------------------------------------------
-- game_events
-- The key type is set by the notebook from the data: NVARCHAR when upstream's
-- natural key was usable, BIGINT when the pipeline generated a surrogate.
-- ---------------------------------------------------------------------------
CREATE TABLE dbo.game_events (
    game_event_id       NVARCHAR(40)    NOT NULL,
    game_id             INT             NOT NULL,
    player_id           INT             NULL,
    club_id             INT             NULL,
    event_date          DATE            NULL,
    event_type          NVARCHAR(20)    NOT NULL,
    minute              SMALLINT        NULL,
    description         NVARCHAR(400)   NULL,
    player_in_id        INT             NULL,
    player_assist_id    INT             NULL,
    CONSTRAINT pk_game_events PRIMARY KEY CLUSTERED (game_event_id),
    CONSTRAINT fk_game_events_game FOREIGN KEY (game_id)
        REFERENCES dbo.games (game_id),
    CONSTRAINT fk_game_events_player FOREIGN KEY (player_id)
        REFERENCES dbo.players (player_id),
    CONSTRAINT fk_game_events_club FOREIGN KEY (club_id)
        REFERENCES dbo.clubs (club_id)
);
GO

-- ---------------------------------------------------------------------------
-- player_valuations: a surrogate IDENTITY key plus the natural key as UNIQUE
-- ---------------------------------------------------------------------------
CREATE TABLE dbo.player_valuations (
    valuation_id            INT             IDENTITY(1,1) NOT NULL,
    player_id               INT             NOT NULL,
    valuation_date          DATE            NOT NULL,
    market_value_in_eur     MONEY           NULL,
    current_club_id         INT             NULL,
    created_at              DATETIME2(3)    NOT NULL CONSTRAINT df_player_valuations_created_at DEFAULT (SYSUTCDATETIME()),
    CONSTRAINT pk_player_valuations PRIMARY KEY CLUSTERED (valuation_id),
    CONSTRAINT uq_player_valuations UNIQUE NONCLUSTERED (player_id, valuation_date),
    CONSTRAINT fk_player_valuations_player FOREIGN KEY (player_id)
        REFERENCES dbo.players (player_id),
    CONSTRAINT fk_player_valuations_club FOREIGN KEY (current_club_id)
        REFERENCES dbo.clubs (club_id)
);
GO

-- ---------------------------------------------------------------------------
-- transfers: IDENTITY key, numbered in transfer-date order at load
-- ---------------------------------------------------------------------------
CREATE TABLE dbo.transfers (
    transfer_id             INT             IDENTITY(1,1) NOT NULL,
    player_id               INT             NOT NULL,
    transfer_date           DATE            NOT NULL,
    transfer_season         NVARCHAR(10)    NULL,
    from_club_id            INT             NULL,
    from_club_name          NVARCHAR(100)   NULL,
    to_club_id              INT             NULL,
    to_club_name            NVARCHAR(100)   NULL,
    transfer_fee            MONEY           NULL,
    market_value_in_eur     MONEY           NULL,
    created_at              DATETIME2(3)    NOT NULL CONSTRAINT df_transfers_created_at DEFAULT (SYSUTCDATETIME()),
    CONSTRAINT pk_transfers PRIMARY KEY CLUSTERED (transfer_id),
    CONSTRAINT fk_transfers_player FOREIGN KEY (player_id)
        REFERENCES dbo.players (player_id),
    CONSTRAINT fk_transfers_from_club FOREIGN KEY (from_club_id)
        REFERENCES dbo.clubs (club_id),
    CONSTRAINT fk_transfers_to_club FOREIGN KEY (to_club_id)
        REFERENCES dbo.clubs (club_id)
);
GO

-- @@AFTER_LOAD
-- Everything below this marker runs after the bulk load: building indexes on
-- full tables is faster, and the bulk load skips constraint checks, so every
-- foreign key is re-validated here and left trusted.
-- ---------------------------------------------------------------------------
ALTER TABLE dbo.clubs             WITH CHECK CHECK CONSTRAINT ALL;
ALTER TABLE dbo.players           WITH CHECK CHECK CONSTRAINT ALL;
ALTER TABLE dbo.games             WITH CHECK CHECK CONSTRAINT ALL;
ALTER TABLE dbo.appearances       WITH CHECK CHECK CONSTRAINT ALL;
ALTER TABLE dbo.game_events       WITH CHECK CHECK CONSTRAINT ALL;
ALTER TABLE dbo.player_valuations WITH CHECK CHECK CONSTRAINT ALL;
ALTER TABLE dbo.transfers         WITH CHECK CHECK CONSTRAINT ALL;
GO

-- ---------------------------------------------------------------------------
-- Nonclustered indexes: the ones the scouting procedures lean on
-- ---------------------------------------------------------------------------
CREATE NONCLUSTERED INDEX ix_players_player_name      ON dbo.players (player_name);
CREATE NONCLUSTERED INDEX ix_players_current_club     ON dbo.players (current_club_id);
CREATE NONCLUSTERED INDEX ix_games_competition_season ON dbo.games (competition_id, season) INCLUDE (game_date);
CREATE NONCLUSTERED INDEX ix_appearances_player       ON dbo.appearances (player_id) INCLUDE (game_id, goals, assists, minutes_played);
CREATE NONCLUSTERED INDEX ix_appearances_game         ON dbo.appearances (game_id);
CREATE NONCLUSTERED INDEX ix_game_events_game         ON dbo.game_events (game_id);
CREATE NONCLUSTERED INDEX ix_transfers_player         ON dbo.transfers (player_id, transfer_date);
GO
