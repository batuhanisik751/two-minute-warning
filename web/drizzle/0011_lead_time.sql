CREATE TABLE "lead_time_conversion" (
	"threshold" integer NOT NULL,
	"signal" text NOT NULL,
	"scope" text NOT NULL,
	"scope_value" text NOT NULL,
	"n_flags" integer NOT NULL,
	"n_added" integer NOT NULL,
	"n_added_after" integer NOT NULL,
	"weeks" integer NOT NULL,
	"flags_per_week" double precision NOT NULL,
	"share_added" double precision,
	"share_added_after" double precision,
	CONSTRAINT "lead_time_conversion_threshold_signal_scope_scope_value_pk" PRIMARY KEY("threshold","signal","scope","scope_value"),
	CONSTRAINT "lead_time_conversion_key_check" CHECK ("lead_time_conversion"."threshold" in (25, 50) and "lead_time_conversion"."signal" in ('must_add', 'spec_plus', 'listed', 'momentum')),
	CONSTRAINT "lead_time_conversion_scope_check" CHECK (("lead_time_conversion"."scope" = 'complete' and "lead_time_conversion"."scope_value" = '') or ("lead_time_conversion"."scope" = 'season' and "lead_time_conversion"."scope_value" ~ '^[0-9]{4}$')),
	CONSTRAINT "lead_time_conversion_counts_check" CHECK ("lead_time_conversion"."n_flags" >= 0 and "lead_time_conversion"."n_added" between 0 and "lead_time_conversion"."n_flags" and "lead_time_conversion"."n_added_after" between 0 and "lead_time_conversion"."n_added" and "lead_time_conversion"."weeks" >= 1 and "lead_time_conversion"."flags_per_week" >= 0 and "lead_time_conversion"."share_added" between 0 and 1 and "lead_time_conversion"."share_added_after" between 0 and 1)
);
--> statement-breakpoint
CREATE TABLE "lead_time_coverage" (
	"season" integer PRIMARY KEY NOT NULL,
	"baseline_date" date,
	"baseline_period" integer NOT NULL,
	"last_period" integer NOT NULL,
	"complete" boolean NOT NULL,
	"in_season_days" integer NOT NULL,
	"first_in_season" date,
	"last_in_season" date,
	"n_players" integer NOT NULL,
	"last_list_week" integer,
	"adds_50" integer,
	"adds_25" integer,
	"in_study" boolean NOT NULL,
	CONSTRAINT "lead_time_coverage_periods_check" CHECK ("lead_time_coverage"."season" >= 2015 and "lead_time_coverage"."baseline_period" between 0 and 22 and "lead_time_coverage"."last_period" between 0 and 22 and "lead_time_coverage"."last_list_week" between 1 and 22),
	CONSTRAINT "lead_time_coverage_counts_check" CHECK ("lead_time_coverage"."in_season_days" >= 0 and "lead_time_coverage"."n_players" >= 0 and "lead_time_coverage"."adds_50" >= 0 and "lead_time_coverage"."adds_25" >= 0 and (not "lead_time_coverage"."complete" or "lead_time_coverage"."in_season_days" > 0))
);
--> statement-breakpoint
CREATE TABLE "lead_time_h2h" (
	"threshold" integer NOT NULL,
	"level" text NOT NULL,
	"h2h" text NOT NULL,
	"n" integer NOT NULL,
	"n_radar_earlier" integer NOT NULL,
	"share" double precision NOT NULL,
	CONSTRAINT "lead_time_h2h_threshold_level_h2h_pk" PRIMARY KEY("threshold","level","h2h"),
	CONSTRAINT "lead_time_h2h_key_check" CHECK ("lead_time_h2h"."threshold" in (25, 50) and "lead_time_h2h"."level" in ('must_add', 'spec_plus', 'listed') and "lead_time_h2h"."h2h" in ('radar_only', 'momentum_only', 'both', 'neither')),
	CONSTRAINT "lead_time_h2h_counts_check" CHECK ("lead_time_h2h"."n" >= 0 and "lead_time_h2h"."n_radar_earlier" between 0 and "lead_time_h2h"."n" and "lead_time_h2h"."share" between 0 and 1)
);
--> statement-breakpoint
CREATE TABLE "lead_time_hist" (
	"threshold" integer NOT NULL,
	"signal" text NOT NULL,
	"lead" integer NOT NULL,
	"n" integer NOT NULL,
	CONSTRAINT "lead_time_hist_threshold_signal_lead_pk" PRIMARY KEY("threshold","signal","lead"),
	CONSTRAINT "lead_time_hist_check" CHECK ("lead_time_hist"."threshold" in (25, 50) and "lead_time_hist"."signal" in ('must_add', 'spec_plus', 'listed', 'momentum') and "lead_time_hist"."lead" between -6 and 10 and "lead_time_hist"."n" >= 1)
);
--> statement-breakpoint
CREATE TABLE "lead_time_reverse" (
	"threshold" integer NOT NULL,
	"signal" text NOT NULL,
	"scope" text NOT NULL,
	"scope_value" text NOT NULL,
	"state" text NOT NULL,
	"n" integer NOT NULL,
	"hits" integer,
	"hit_rate" double precision,
	"lead_median" double precision,
	CONSTRAINT "lead_time_reverse_threshold_signal_scope_scope_value_state_pk" PRIMARY KEY("threshold","signal","scope","scope_value","state"),
	CONSTRAINT "lead_time_reverse_key_check" CHECK ("lead_time_reverse"."threshold" in (25, 50) and "lead_time_reverse"."signal" in ('must_add', 'spec_plus', 'listed', 'momentum') and "lead_time_reverse"."state" in ('already', 'added_later', 'never')),
	CONSTRAINT "lead_time_reverse_scope_check" CHECK (("lead_time_reverse"."scope" = 'complete' and "lead_time_reverse"."scope_value" = '') or ("lead_time_reverse"."scope" = 'season' and "lead_time_reverse"."scope_value" ~ '^[0-9]{4}$')),
	CONSTRAINT "lead_time_reverse_counts_check" CHECK ("lead_time_reverse"."n" >= 1 and "lead_time_reverse"."hits" between 0 and "lead_time_reverse"."n" and "lead_time_reverse"."hit_rate" between 0 and 1)
);
--> statement-breakpoint
CREATE TABLE "lead_time_summary" (
	"threshold" integer NOT NULL,
	"signal" text NOT NULL,
	"scope" text NOT NULL,
	"scope_value" text NOT NULL,
	"n_adds" integer NOT NULL,
	"n_before" integer,
	"n_same" integer,
	"n_after" integer,
	"n_never" integer,
	"n_never_out_of_pool" integer,
	"share_before" double precision,
	"share_same" double precision,
	"share_after" double precision,
	"share_never" double precision,
	"lead_median" double precision,
	"lead_q1" double precision,
	"lead_q3" double precision,
	"nearest_median" double precision,
	"share_before_4" double precision,
	CONSTRAINT "lead_time_summary_threshold_signal_scope_scope_value_pk" PRIMARY KEY("threshold","signal","scope","scope_value"),
	CONSTRAINT "lead_time_summary_key_check" CHECK ("lead_time_summary"."threshold" in (25, 50) and "lead_time_summary"."signal" in ('must_add', 'spec_plus', 'listed', 'momentum')),
	CONSTRAINT "lead_time_summary_scope_check" CHECK (("lead_time_summary"."scope" = 'complete' and "lead_time_summary"."scope_value" = '') or ("lead_time_summary"."scope" = 'season' and "lead_time_summary"."scope_value" ~ '^[0-9]{4}$') or ("lead_time_summary"."scope" = 'position' and "lead_time_summary"."scope_value" in ('QB', 'RB', 'WR', 'TE'))),
	CONSTRAINT "lead_time_summary_counts_check" CHECK ("lead_time_summary"."n_adds" >= 0 and ("lead_time_summary"."n_before" is null or "lead_time_summary"."n_before" + "lead_time_summary"."n_same" + "lead_time_summary"."n_after" + "lead_time_summary"."n_never" = "lead_time_summary"."n_adds") and "lead_time_summary"."n_never_out_of_pool" <= "lead_time_summary"."n_never"),
	CONSTRAINT "lead_time_summary_share_check" CHECK ("lead_time_summary"."share_before" between 0 and 1 and "lead_time_summary"."share_same" between 0 and 1 and "lead_time_summary"."share_after" between 0 and 1 and "lead_time_summary"."share_never" between 0 and 1 and "lead_time_summary"."share_before_4" between 0 and 1)
);
