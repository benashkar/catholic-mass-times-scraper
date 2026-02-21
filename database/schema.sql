-- =============================================================================
-- Catholic Mass Times Scraper — PostgreSQL Schema
-- =============================================================================
--
-- DESIGN PHILOSOPHY:
--     Maximum normalization. Every field that can be a lookup table, enum, or
--     boolean IS one. No free-text strings where structured data exists.
--     This makes queries fast, filters predictable, and data consistent.
--
-- NAMING CONVENTIONS:
--     - Tables: snake_case, singular nouns (church, service, community)
--     - Primary keys: {table}_id (SERIAL or UUID)
--     - Foreign keys: {referenced_table}_id
--     - Lookup tables: lk_{domain} (e.g., lk_service_category, lk_language)
--     - Junction tables: {table1}_{table2} (e.g., church_community)
--     - Timestamps: created_at, updated_at (always UTC)
--     - Booleans: is_{adjective} or has_{noun} (e.g., is_vigil, has_livestream)
--
-- HOW TO RUN:
--     psql -U your_user -d catholic_mass_times -f database/schema.sql
--
-- =============================================================================


-- Drop existing tables in reverse dependency order (for re-running)
DROP TABLE IF EXISTS scrape_log CASCADE;
DROP TABLE IF EXISTS service_note_tag CASCADE;
DROP TABLE IF EXISTS lk_note_tag CASCADE;
DROP TABLE IF EXISTS service CASCADE;
DROP TABLE IF EXISTS church_community CASCADE;
DROP TABLE IF EXISTS clergy_assignment CASCADE;
DROP TABLE IF EXISTS clergy CASCADE;
DROP TABLE IF EXISTS church CASCADE;
DROP TABLE IF EXISTS community CASCADE;
DROP TABLE IF EXISTS lk_recurrence_pattern CASCADE;
DROP TABLE IF EXISTS lk_time_relation CASCADE;
DROP TABLE IF EXISTS lk_schedule_type CASCADE;
DROP TABLE IF EXISTS lk_service_category CASCADE;
DROP TABLE IF EXISTS lk_language CASCADE;
DROP TABLE IF EXISTS lk_day_of_week CASCADE;
DROP TABLE IF EXISTS lk_clergy_role CASCADE;
DROP TABLE IF EXISTS lk_state CASCADE;
DROP TABLE IF EXISTS lk_diocese CASCADE;


-- =============================================================================
-- LOOKUP TABLES
-- =============================================================================
-- These are small, rarely-changing reference tables. Each has a short code
-- (used as the PK for compact storage and readable joins) and a human-readable
-- display label. Using these instead of raw strings means:
--   1. Typos are impossible (FK constraint catches them)
--   2. Queries are faster (short codes vs. long strings)
--   3. Display labels can change without touching service data
-- =============================================================================


-- ---------------------------------------------------------------------------
-- lk_state — US states and territories
-- ---------------------------------------------------------------------------
-- We store the 2-letter USPS code as the PK since it's already a natural key
-- that everyone uses. No need for a surrogate ID.
--
-- WHY A TABLE INSTEAD OF JUST A VARCHAR:
--     Prevents "Ohio" vs "OH" vs "ohio" inconsistencies.
--     Phase 2+ adds more states, so we just INSERT new rows.
-- ---------------------------------------------------------------------------
CREATE TABLE lk_state (
    state_code  CHAR(2)     PRIMARY KEY,    -- "OH", "PA", "IN", etc.
    state_name  VARCHAR(50) NOT NULL UNIQUE  -- "Ohio", "Pennsylvania", etc.
);

INSERT INTO lk_state (state_code, state_name) VALUES
    ('OH', 'Ohio'),
    ('PA', 'Pennsylvania'),
    ('IN', 'Indiana'),
    ('KY', 'Kentucky'),
    ('WV', 'West Virginia'),
    ('MI', 'Michigan');
-- Add more states as we expand nationally


-- ---------------------------------------------------------------------------
-- lk_diocese — Catholic dioceses
-- ---------------------------------------------------------------------------
-- A diocese is a geographic district under a bishop's jurisdiction.
-- Every Catholic church belongs to exactly one diocese.
-- We include this for future use when we want to group/filter churches
-- by diocese, cross-reference with diocesan directories, etc.
-- ---------------------------------------------------------------------------
CREATE TABLE lk_diocese (
    diocese_id   SERIAL      PRIMARY KEY,
    diocese_name VARCHAR(150) NOT NULL UNIQUE,   -- "Diocese of Columbus"
    state_code   CHAR(2)     REFERENCES lk_state(state_code),
    website      VARCHAR(500)                     -- "https://columbuscatholic.org"
);

INSERT INTO lk_diocese (diocese_name, state_code, website) VALUES
    ('Diocese of Columbus', 'OH', 'https://columbuscatholic.org');


-- ---------------------------------------------------------------------------
-- lk_service_category — What kind of service this is
-- ---------------------------------------------------------------------------
-- CatholicIndex organizes services into these categories.
-- Using a lookup table lets us add new categories without schema changes.
--
-- Current categories found in the data (7):
--   Mass, Confession, Adoration, Devotions, Other, Education, Community
-- ---------------------------------------------------------------------------
CREATE TABLE lk_service_category (
    category_code VARCHAR(20) PRIMARY KEY,      -- "mass", "confession", etc.
    display_name  VARCHAR(50) NOT NULL UNIQUE,   -- "Mass", "Confession", etc.
    sort_order    SMALLINT    NOT NULL DEFAULT 99 -- For display ordering
);

INSERT INTO lk_service_category (category_code, display_name, sort_order) VALUES
    ('mass',        'Mass',        1),
    ('confession',  'Confession',  2),
    ('adoration',   'Adoration',   3),
    ('devotions',   'Devotions',   4),
    ('education',   'Education',   5),
    ('community',   'Community',   6),
    ('other',       'Other',       99);


-- ---------------------------------------------------------------------------
-- lk_schedule_type — When/how often the service occurs
-- ---------------------------------------------------------------------------
-- This describes the recurrence pattern at a high level:
--   - "sunday" = every Sunday
--   - "saturday" = every Saturday (typically vigil masses)
--   - "weekday" = every weekday (Mon-Fri)
--   - "specific_weekday" = a specific day each week (e.g., every Tuesday)
--   - "monthly" = once a month (combined with lk_recurrence_pattern)
--   - "special_event" = one-time event on a specific date
--   - "parish_event" = parish-organized event (fish fry, festival, etc.)
--   - "other" = doesn't fit the above (e.g., "by appointment")
-- ---------------------------------------------------------------------------
CREATE TABLE lk_schedule_type (
    schedule_type_code VARCHAR(20) PRIMARY KEY,
    display_name       VARCHAR(50) NOT NULL UNIQUE,
    description        TEXT
);

INSERT INTO lk_schedule_type (schedule_type_code, display_name, description) VALUES
    ('sunday',           'Sunday',           'Recurring every Sunday'),
    ('saturday',         'Saturday',         'Recurring every Saturday (typically vigil)'),
    ('weekday',          'Weekday',          'Recurring every weekday (Mon-Fri)'),
    ('specific_weekday', 'Specific Weekday', 'Recurring on one specific weekday each week'),
    ('monthly',          'Monthly',          'Recurring once per month (see recurrence_pattern)'),
    ('special_event',    'Special Event',    'One-time event on a specific date (Holy Day, etc.)'),
    ('parish_event',     'Parish Event',     'Parish-organized community event'),
    ('other',            'Other',            'Does not fit standard schedule patterns');


-- ---------------------------------------------------------------------------
-- lk_language — Language the service is conducted in
-- ---------------------------------------------------------------------------
-- Most services are in English (stored as NULL = default/English).
-- When a service is explicitly tagged with a language, we store it here.
--
-- Found in data: english, spanish, bilingual, latin, (null = default English)
-- ---------------------------------------------------------------------------
CREATE TABLE lk_language (
    language_code VARCHAR(20) PRIMARY KEY,       -- "en", "es", "la", "bi"
    display_name  VARCHAR(50) NOT NULL UNIQUE,    -- "English", "Spanish", etc.
    iso_639_1     CHAR(2)                         -- Standard ISO language code
);

INSERT INTO lk_language (language_code, display_name, iso_639_1) VALUES
    ('en', 'English',    'en'),
    ('es', 'Spanish',    'es'),
    ('la', 'Latin',      'la'),
    ('bi', 'Bilingual',  NULL),  -- Not a standard ISO code; English + Spanish mix
    ('vi', 'Vietnamese', 'vi'),
    ('ko', 'Korean',     'ko'),
    ('pl', 'Polish',     'pl'),
    ('pt', 'Portuguese', 'pt');
-- Expand as we encounter more languages nationally


-- ---------------------------------------------------------------------------
-- lk_day_of_week — Days of the week
-- ---------------------------------------------------------------------------
-- Using a lookup table with an integer sort key makes "order by day" trivial.
-- The 3-letter code matches what CatholicIndex uses in its data.
-- ---------------------------------------------------------------------------
CREATE TABLE lk_day_of_week (
    day_code     CHAR(3)  PRIMARY KEY,           -- "Mon", "Tue", etc.
    day_name     VARCHAR(10) NOT NULL UNIQUE,     -- "Monday", "Tuesday", etc.
    sort_order   SMALLINT    NOT NULL UNIQUE,     -- 1=Mon, 2=Tue, ..., 7=Sun
    is_weekend   BOOLEAN     NOT NULL DEFAULT FALSE
);

INSERT INTO lk_day_of_week (day_code, day_name, sort_order, is_weekend) VALUES
    ('Mon', 'Monday',    1, FALSE),
    ('Tue', 'Tuesday',   2, FALSE),
    ('Wed', 'Wednesday', 3, FALSE),
    ('Thu', 'Thursday',  4, FALSE),
    ('Fri', 'Friday',    5, FALSE),
    ('Sat', 'Saturday',  6, TRUE),
    ('Sun', 'Sunday',    7, TRUE);


-- ---------------------------------------------------------------------------
-- lk_recurrence_pattern — Monthly/periodic recurrence patterns
-- ---------------------------------------------------------------------------
-- Used for services that happen on a specific pattern, like "First Friday"
-- or "First Saturday". Combined with schedule_type = 'monthly'.
--
-- Found in data:
--   first_friday_(recurring), first_saturday_(recurring),
--   first_sunday, thursday_(before_first_friday)
-- ---------------------------------------------------------------------------
CREATE TABLE lk_recurrence_pattern (
    pattern_code  VARCHAR(50)  PRIMARY KEY,       -- "first_friday" etc.
    display_name  VARCHAR(100) NOT NULL UNIQUE,
    description   TEXT
);

INSERT INTO lk_recurrence_pattern (pattern_code, display_name, description) VALUES
    ('first_friday',                'First Friday',                  'First Friday of each month'),
    ('first_saturday',              'First Saturday',                'First Saturday of each month'),
    ('first_sunday',                'First Sunday',                  'First Sunday of each month'),
    ('thursday_before_first_friday','Thursday Before First Friday',  'Thursday evening before First Friday'),
    ('third_sunday',                'Third Sunday',                  'Third Sunday of each month'),
    ('last_wednesday',              'Last Wednesday',                'Last Wednesday of each month');
-- Expand as we encounter more patterns nationally


-- ---------------------------------------------------------------------------
-- lk_time_relation — Relative time positioning
-- ---------------------------------------------------------------------------
-- Some services don't have a fixed start time — instead they're defined
-- relative to another service. For example:
--   "Confession: after 08:30 Mass"
--   "Rosary: before Mass"
--
-- The service table stores which service it's relative to (reference_service)
-- and optionally an offset in minutes.
-- ---------------------------------------------------------------------------
CREATE TABLE lk_time_relation (
    relation_code VARCHAR(10) PRIMARY KEY,        -- "before", "after"
    display_name  VARCHAR(20) NOT NULL UNIQUE
);

INSERT INTO lk_time_relation (relation_code, display_name) VALUES
    ('before', 'Before'),
    ('after',  'After');


-- ---------------------------------------------------------------------------
-- lk_clergy_role — Roles for priests, deacons, etc.
-- ---------------------------------------------------------------------------
-- A lookup for the different roles a member of clergy can hold at a parish.
-- This supports the future pastor/leader data from diocesan directories.
-- ---------------------------------------------------------------------------
CREATE TABLE lk_clergy_role (
    role_code    VARCHAR(30) PRIMARY KEY,
    display_name VARCHAR(50) NOT NULL UNIQUE,
    sort_order   SMALLINT    NOT NULL DEFAULT 99
);

INSERT INTO lk_clergy_role (role_code, display_name, sort_order) VALUES
    ('pastor',           'Pastor',            1),
    ('parochial_vicar',  'Parochial Vicar',   2),
    ('deacon',           'Deacon',            3),
    ('administrator',    'Administrator',     4),
    ('priest_moderator', 'Priest Moderator',  5),
    ('senior_parochial', 'Senior Parochial Vicar', 6),
    ('retired',          'Retired',           99);


-- ---------------------------------------------------------------------------
-- lk_note_tag — Structured tags extracted from free-text notes
-- ---------------------------------------------------------------------------
-- The CatholicIndex "notes" field is free-text, but many notes contain
-- structured information that can be parsed into boolean tags.
-- For example: "vigil", "by appointment", "24 hours", "school Mass"
--
-- Rather than storing these as booleans on the service table (which would
-- require schema changes for each new tag), we use a many-to-many junction
-- table (service_note_tag). This is the ultimate normalized approach.
-- ---------------------------------------------------------------------------
CREATE TABLE lk_note_tag (
    tag_code     VARCHAR(30) PRIMARY KEY,
    display_name VARCHAR(100) NOT NULL UNIQUE,
    description  TEXT
);

INSERT INTO lk_note_tag (tag_code, display_name, description) VALUES
    ('vigil',           'Vigil Mass',                'Saturday evening vigil anticipating Sunday'),
    ('by_appointment',  'By Appointment',            'Available by appointment with the priest'),
    ('24_hours',        '24 Hours',                  'Available 24 hours (perpetual adoration)'),
    ('school_mass',     'School Mass',               'Held at/for the parish school, public welcome'),
    ('holy_day',        'Holy Day of Obligation',    'Special Mass for a Holy Day of Obligation'),
    ('bilingual',       'Bilingual Service',         'Conducted in two languages'),
    ('exposition',      'Exposition',                'Includes Eucharistic exposition'),
    ('benediction',     'Benediction',               'Includes Benediction of the Blessed Sacrament'),
    ('livestreamed',    'Livestreamed',              'Available via online livestream'),
    ('no_mass_after',   'No Mass After',             'Note that regular Mass is not held after'),
    ('seasonal',        'Seasonal',                  'Only during certain liturgical seasons (Lent, Advent)'),
    ('see_bulletin',    'See Bulletin',              'Check parish bulletin for details'),
    ('public_welcome',  'Public Welcome',            'Explicitly noted that public is welcome'),
    ('first_friday',    'First Friday Service',      'Associated with First Friday devotion'),
    ('first_saturday',  'First Saturday Service',    'Associated with First Saturday devotion'),
    ('all_day',         'All Day',                   'Available all day / no fixed end time');


-- =============================================================================
-- CORE ENTITY TABLES
-- =============================================================================


-- ---------------------------------------------------------------------------
-- community — Target communities we're serving with mass time data
-- ---------------------------------------------------------------------------
-- These are the neighborhoods, townships, and small cities that CR Community
-- News covers. Each community may be served by many churches, and each
-- church may serve many communities (many-to-many via church_community).
-- ---------------------------------------------------------------------------
CREATE TABLE community (
    community_id    SERIAL       PRIMARY KEY,
    name            VARCHAR(100) NOT NULL,            -- "Grove City"
    county          VARCHAR(50)  NOT NULL,            -- "Franklin"
    state_code      CHAR(2)      NOT NULL REFERENCES lk_state(state_code),
    zip_codes       VARCHAR(50)  NOT NULL,            -- "43123" (comma-separated if multiple)
    fallback_city   VARCHAR(100),                     -- "Columbus" (if no CatholicIndex page)
    latitude        DECIMAL(10,7),                    -- Center point for distance calculations
    longitude       DECIMAL(10,7),
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE, -- Can deactivate without deleting
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    UNIQUE (name, state_code)
);

-- Index for geographic queries (find communities near a point)
CREATE INDEX idx_community_geo ON community (latitude, longitude);


-- ---------------------------------------------------------------------------
-- church — Individual Catholic churches/parishes
-- ---------------------------------------------------------------------------
-- The core entity. Each row is one physical church location.
-- The slug is the CatholicIndex unique identifier and serves as our
-- natural key for deduplication.
--
-- FIELD NOTES:
--   - slug: CatholicIndex URL slug, globally unique per church
--   - diocese_id: Foreign key to lk_diocese (for grouping/filtering)
--   - has_perpetual_adoration: Explicitly flagged in CatholicIndex data
--   - has_livestream: Derived from livestream_url being non-null
--   - schedule_updated_at: When CatholicIndex last confirmed the schedule
--   - source_url: The CatholicIndex page we scraped this from
--   - last_scraped_at: When WE last pulled data for this church
-- ---------------------------------------------------------------------------
CREATE TABLE church (
    church_id               SERIAL          PRIMARY KEY,
    slug                    VARCHAR(200)    NOT NULL UNIQUE,  -- CatholicIndex slug (natural key)
    name                    VARCHAR(200)    NOT NULL,
    street                  VARCHAR(200),
    city                    VARCHAR(100)    NOT NULL,
    state_code              CHAR(2)         NOT NULL REFERENCES lk_state(state_code),
    postal_code             VARCHAR(20),
    latitude                DECIMAL(10,7),
    longitude               DECIMAL(10,7),
    phone                   VARCHAR(30),
    website_url             VARCHAR(500),
    livestream_url          VARCHAR(500),
    diocese_id              INTEGER         REFERENCES lk_diocese(diocese_id),
    has_perpetual_adoration BOOLEAN         NOT NULL DEFAULT FALSE,
    has_livestream          BOOLEAN         NOT NULL DEFAULT FALSE,
    is_active               BOOLEAN         NOT NULL DEFAULT TRUE,
    mass_count              SMALLINT        NOT NULL DEFAULT 0, -- Summary count from discovery
    confession_count        SMALLINT        NOT NULL DEFAULT 0,
    adoration_count         SMALLINT        NOT NULL DEFAULT 0,
    stream_count            SMALLINT        NOT NULL DEFAULT 0,
    schedule_updated_at     TIMESTAMPTZ,    -- When CatholicIndex last updated the schedule
    source_url              VARCHAR(500),   -- CatholicIndex page URL
    community_insights      TEXT,           -- User-submitted reviews (from CatholicIndex)
    last_scraped_at         TIMESTAMPTZ,    -- When we last pulled data
    created_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- Indexes for common query patterns
CREATE INDEX idx_church_city       ON church (city);
CREATE INDEX idx_church_state      ON church (state_code);
CREATE INDEX idx_church_diocese    ON church (diocese_id);
CREATE INDEX idx_church_geo        ON church (latitude, longitude);
CREATE INDEX idx_church_name       ON church (name);
CREATE INDEX idx_church_postal     ON church (postal_code);


-- ---------------------------------------------------------------------------
-- church_community — Which churches serve which communities (many-to-many)
-- ---------------------------------------------------------------------------
-- A church within 25 miles of a community center is considered to serve it.
-- distance_miles is the straight-line distance from the church to the
-- community center (as reported by CatholicIndex).
-- ---------------------------------------------------------------------------
CREATE TABLE church_community (
    church_id       INTEGER NOT NULL REFERENCES church(church_id) ON DELETE CASCADE,
    community_id    INTEGER NOT NULL REFERENCES community(community_id) ON DELETE CASCADE,
    distance_miles  DECIMAL(5,1),  -- Distance from community center to church
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (church_id, community_id)
);


-- ---------------------------------------------------------------------------
-- clergy — Priests, deacons, and other parish leaders
-- ---------------------------------------------------------------------------
-- NOT YET POPULATED — CatholicIndex does not provide clergy data.
-- This table is ready for when we add a clergy data source:
--   - Diocese assignment announcements (columbuscatholic.org)
--   - Commercial directories (CatholicParishDirectory.com)
--   - Individual parish websites
--
-- A person can be assigned to multiple churches over time (see clergy_assignment).
-- ---------------------------------------------------------------------------
CREATE TABLE clergy (
    clergy_id    SERIAL       PRIMARY KEY,
    prefix       VARCHAR(20),                     -- "Rev.", "Msgr.", "Deacon"
    first_name   VARCHAR(100) NOT NULL,
    middle_name  VARCHAR(100),
    last_name    VARCHAR(100) NOT NULL,
    suffix       VARCHAR(20),                     -- "Jr.", "III", etc.
    is_active    BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    -- Prevent duplicate people (same name could be different people, but this
    -- catches obvious duplicates; human review needed for edge cases)
    UNIQUE (first_name, last_name, prefix)
);

CREATE INDEX idx_clergy_name ON clergy (last_name, first_name);


-- ---------------------------------------------------------------------------
-- clergy_assignment — Which clergy serve at which church and in what role
-- ---------------------------------------------------------------------------
-- Tracks the assignment of clergy to churches over time. A priest might
-- move from one parish to another, so we track effective dates.
-- ---------------------------------------------------------------------------
CREATE TABLE clergy_assignment (
    assignment_id   SERIAL      PRIMARY KEY,
    clergy_id       INTEGER     NOT NULL REFERENCES clergy(clergy_id) ON DELETE CASCADE,
    church_id       INTEGER     NOT NULL REFERENCES church(church_id) ON DELETE CASCADE,
    role_code       VARCHAR(30) NOT NULL REFERENCES lk_clergy_role(role_code),
    effective_date  DATE,                       -- When this assignment started
    end_date        DATE,                       -- NULL = currently assigned
    is_current      BOOLEAN     NOT NULL DEFAULT TRUE,
    source          VARCHAR(200),               -- Where we got this info (URL, etc.)
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_clergy_assignment_church ON clergy_assignment (church_id, is_current);
CREATE INDEX idx_clergy_assignment_clergy ON clergy_assignment (clergy_id, is_current);


-- ---------------------------------------------------------------------------
-- service — Individual church services (mass times, confessions, etc.)
-- ---------------------------------------------------------------------------
-- This is the BIG table — every mass time, confession slot, adoration hour,
-- rosary, novena, etc. is one row.
--
-- DESIGN DECISIONS:
--   - source_service_id: The ID from CatholicIndex (for dedup on re-scrape)
--   - category_code: FK to lk_service_category (not a string!)
--   - schedule_type_code: FK to lk_schedule_type (not a string!)
--   - language_code: FK to lk_language, NULL = default (English)
--   - day_code: FK to lk_day_of_week, NULL for special events
--   - time_start/time_end: TIME type (not string!), NULL for relative-time services
--   - event_date: DATE type for one-time special events, NULL for recurring
--   - pattern_code: FK to lk_recurrence_pattern for monthly services
--   - relation_code: FK to lk_time_relation for "before/after Mass" services
--   - reference_service: What this service is relative to (free text: "Mass")
--   - offset_minutes: How many minutes before/after the reference service
--   - location: Free text for multi-site parishes ("Church", "School Gym", etc.)
--   - display_name: Human-readable name from CatholicIndex ("Holy Mass", "Vigil")
--   - notes_raw: The original notes string from CatholicIndex (preserved for audit)
-- ---------------------------------------------------------------------------
CREATE TABLE service (
    service_id          SERIAL       PRIMARY KEY,
    church_id           INTEGER      NOT NULL REFERENCES church(church_id) ON DELETE CASCADE,
    source_service_id   VARCHAR(50)  NOT NULL,  -- CatholicIndex service ID (for dedup)
    category_code       VARCHAR(20)  NOT NULL REFERENCES lk_service_category(category_code),
    schedule_type_code  VARCHAR(20)  NOT NULL REFERENCES lk_schedule_type(schedule_type_code),
    day_code            CHAR(3)      REFERENCES lk_day_of_week(day_code),       -- NULL for special events
    time_start          TIME,                   -- NULL if relative-time only
    time_end            TIME,                   -- NULL if no defined end time
    event_date          DATE,                   -- NULL for recurring services
    language_code       VARCHAR(20)  REFERENCES lk_language(language_code),     -- NULL = English (default)
    pattern_code        VARCHAR(50)  REFERENCES lk_recurrence_pattern(pattern_code),
    relation_code       VARCHAR(10)  REFERENCES lk_time_relation(relation_code),
    reference_service   VARCHAR(50),            -- "Mass" — what this is relative to
    offset_minutes      SMALLINT,               -- Minutes before/after reference
    location            VARCHAR(200),           -- "Church", "School Gym", etc.
    display_name        VARCHAR(200) NOT NULL,  -- "Holy Mass", "Vigil", "Rosary", etc.
    notes_raw           TEXT,                   -- Original notes string from source (audit trail)
    is_active           BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    -- Prevent duplicate services from re-scraping the same church
    UNIQUE (church_id, source_service_id)
);

-- Indexes for the most common query patterns:
-- "Show me all Sunday masses near Grove City"
CREATE INDEX idx_service_category   ON service (category_code);
CREATE INDEX idx_service_day        ON service (day_code);
CREATE INDEX idx_service_church     ON service (church_id);
CREATE INDEX idx_service_schedule   ON service (schedule_type_code);
CREATE INDEX idx_service_language   ON service (language_code);
CREATE INDEX idx_service_event_date ON service (event_date) WHERE event_date IS NOT NULL;
CREATE INDEX idx_service_active     ON service (is_active) WHERE is_active = TRUE;

-- Composite index for the most common query: "Sunday masses at active churches"
CREATE INDEX idx_service_cat_day_active
    ON service (category_code, day_code, is_active)
    WHERE is_active = TRUE;


-- ---------------------------------------------------------------------------
-- service_note_tag — Structured tags parsed from service notes (many-to-many)
-- ---------------------------------------------------------------------------
-- Instead of adding boolean columns to the service table for every possible
-- tag (is_vigil, is_by_appointment, is_24_hours, etc.), we use a junction
-- table. This is more flexible — new tags can be added without schema changes.
--
-- The ETL pipeline will parse the notes_raw field and INSERT appropriate tags.
-- For example:
--   notes_raw = "vigil"              → tag_code = 'vigil'
--   notes_raw = "Or anytime by appointment" → tag_code = 'by_appointment'
--   notes_raw = "24 hours"           → tag_code = '24_hours'
-- ---------------------------------------------------------------------------
CREATE TABLE service_note_tag (
    service_id  INTEGER     NOT NULL REFERENCES service(service_id) ON DELETE CASCADE,
    tag_code    VARCHAR(30) NOT NULL REFERENCES lk_note_tag(tag_code),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (service_id, tag_code)
);

CREATE INDEX idx_service_note_tag_tag ON service_note_tag (tag_code);


-- ---------------------------------------------------------------------------
-- scrape_log — Audit trail of every scrape run
-- ---------------------------------------------------------------------------
-- Tracks when we scraped, what we scraped, and whether it succeeded.
-- Essential for debugging, monitoring, and knowing when data is stale.
-- ---------------------------------------------------------------------------
CREATE TABLE scrape_log (
    scrape_id           SERIAL       PRIMARY KEY,
    scrape_type         VARCHAR(30)  NOT NULL,  -- 'discovery', 'detail', 'full_refresh'
    started_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    completed_at        TIMESTAMPTZ,
    status              VARCHAR(20)  NOT NULL DEFAULT 'running', -- 'running','success','failed','partial'
    communities_scraped SMALLINT     DEFAULT 0,
    churches_scraped    SMALLINT     DEFAULT 0,
    services_upserted   INTEGER      DEFAULT 0,
    errors              TEXT,                   -- JSON array of error messages
    notes               TEXT
);


-- =============================================================================
-- HELPER VIEWS
-- =============================================================================
-- These views provide convenient pre-joined queries for common use cases.
-- They don't store data — they're just saved queries.
-- =============================================================================


-- ---------------------------------------------------------------------------
-- v_sunday_masses — All active Sunday masses with church details
-- ---------------------------------------------------------------------------
-- This is the bread-and-butter query for the newspaper listing:
-- "Show me every Sunday mass at every church, sorted by time"
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_sunday_masses AS
SELECT
    c.name              AS church_name,
    c.street,
    c.city,
    s_state.state_name,
    c.postal_code,
    c.phone,
    s.time_start,
    s.display_name,
    lang.display_name   AS language,
    s.location,
    s.notes_raw,
    c.slug              AS church_slug,
    c.church_id
FROM service s
JOIN church c           ON c.church_id = s.church_id
JOIN lk_state s_state   ON s_state.state_code = c.state_code
LEFT JOIN lk_language lang ON lang.language_code = s.language_code
WHERE s.category_code = 'mass'
  AND s.day_code = 'Sun'
  AND s.is_active = TRUE
  AND c.is_active = TRUE
ORDER BY s.time_start, c.name;


-- ---------------------------------------------------------------------------
-- v_weekly_schedule — Full weekly schedule for all churches
-- ---------------------------------------------------------------------------
-- Shows every recurring service, organized by church, then by day and time.
-- This feeds the "full parish schedule" view in the newspaper listing.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_weekly_schedule AS
SELECT
    c.church_id,
    c.name              AS church_name,
    c.city,
    c.phone,
    cat.display_name    AS category,
    cat.sort_order      AS category_sort,
    dow.day_name,
    dow.sort_order      AS day_sort,
    s.time_start,
    s.time_end,
    s.display_name      AS service_name,
    lang.display_name   AS language,
    s.location,
    s.notes_raw,
    stype.display_name  AS schedule_type
FROM service s
JOIN church c                       ON c.church_id = s.church_id
JOIN lk_service_category cat        ON cat.category_code = s.category_code
LEFT JOIN lk_day_of_week dow        ON dow.day_code = s.day_code
LEFT JOIN lk_language lang          ON lang.language_code = s.language_code
LEFT JOIN lk_schedule_type stype    ON stype.schedule_type_code = s.schedule_type_code
WHERE s.is_active = TRUE
  AND c.is_active = TRUE
  AND s.event_date IS NULL  -- Exclude one-time events; show only recurring
ORDER BY c.name, cat.sort_order, dow.sort_order, s.time_start;


-- ---------------------------------------------------------------------------
-- v_confession_times — All active confession times
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_confession_times AS
SELECT
    c.name              AS church_name,
    c.city,
    c.phone,
    dow.day_name,
    dow.sort_order      AS day_sort,
    s.time_start,
    s.time_end,
    rel.display_name    AS time_relation,
    s.reference_service,
    s.notes_raw,
    c.church_id
FROM service s
JOIN church c                       ON c.church_id = s.church_id
LEFT JOIN lk_day_of_week dow        ON dow.day_code = s.day_code
LEFT JOIN lk_time_relation rel      ON rel.relation_code = s.relation_code
WHERE s.category_code = 'confession'
  AND s.is_active = TRUE
  AND c.is_active = TRUE
ORDER BY dow.sort_order, s.time_start, c.name;


-- ---------------------------------------------------------------------------
-- v_church_summary — Dashboard view of all churches with service counts
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_church_summary AS
SELECT
    c.church_id,
    c.name,
    c.city,
    s_state.state_name,
    c.postal_code,
    c.phone,
    c.has_perpetual_adoration,
    c.has_livestream,
    c.schedule_updated_at,
    c.last_scraped_at,
    COUNT(s.service_id) FILTER (WHERE s.category_code = 'mass')       AS mass_count,
    COUNT(s.service_id) FILTER (WHERE s.category_code = 'confession') AS confession_count,
    COUNT(s.service_id) FILTER (WHERE s.category_code = 'adoration')  AS adoration_count,
    COUNT(s.service_id) FILTER (WHERE s.category_code = 'devotions')  AS devotion_count,
    COUNT(s.service_id)                                                AS total_services,
    STRING_AGG(DISTINCT comm.name, '; ' ORDER BY comm.name)           AS serving_communities
FROM church c
JOIN lk_state s_state ON s_state.state_code = c.state_code
LEFT JOIN service s ON s.church_id = c.church_id AND s.is_active = TRUE
LEFT JOIN church_community cc ON cc.church_id = c.church_id
LEFT JOIN community comm ON comm.community_id = cc.community_id
WHERE c.is_active = TRUE
GROUP BY c.church_id, c.name, c.city, s_state.state_name, c.postal_code,
         c.phone, c.has_perpetual_adoration, c.has_livestream,
         c.schedule_updated_at, c.last_scraped_at
ORDER BY c.city, c.name;


-- =============================================================================
-- MAPPING FUNCTIONS (for ETL pipeline)
-- =============================================================================
-- These functions help the Python ETL code map CatholicIndex values to our
-- normalized lookup codes. They're documented here for reference but
-- implemented in the Python ETL layer (src/etl/transformers.py).
--
-- CatholicIndex value    → Our lookup code
-- ─────────────────────────────────────────
-- "Mass"                 → "mass"
-- "Confession"           → "confession"
-- "Adoration"            → "adoration"
-- "Devotions"            → "devotions"
-- "Education"            → "education"
-- "Community"            → "community"
-- "Other"                → "other"
--
-- "english"              → "en"
-- "spanish"              → "es"
-- "latin"                → "la"
-- "bilingual"            → "bi"
-- "vietnamese"           → "vi"
-- NULL                   → NULL (English is the default)
--
-- "first_friday_(recurring)"            → "first_friday"
-- "first_saturday_(recurring)"          → "first_saturday"
-- "first_sunday"                        → "first_sunday"
-- "thursday_(before_first_friday)"      → "thursday_before_first_friday"
--
-- "Ohio"                 → "OH"
-- "Pennsylvania"         → "PA"
-- (state names from CatholicIndex are full names; we map to 2-letter codes)
-- =============================================================================
