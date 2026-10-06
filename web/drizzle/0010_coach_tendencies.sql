CREATE TABLE "coach_tendency_career" (
	"coach_id" text NOT NULL,
	"metric" text NOT NULL,
	"seasons" integer NOT NULL,
	"first_season" integer NOT NULL,
	"last_season" integer NOT NULL,
	"teams" text NOT NULL,
	"value" double precision NOT NULL,
	"sample" integer NOT NULL,
	"league_avg" double precision NOT NULL,
	"vs_league" double precision NOT NULL,
	CONSTRAINT "coach_tendency_career_coach_id_metric_pk" PRIMARY KEY("coach_id","metric"),
	CONSTRAINT "coach_tendency_career_metric_check" CHECK ("coach_tendency_career"."metric" in ('neutral_pass_rate', 'early_down_pass_rate', 'proe', 'neutral_sec_per_play', 'no_huddle_rate', 'shotgun_rate', 'fourth_go_rate', 'fourth_short_go_rate')),
	CONSTRAINT "coach_tendency_career_value_check" CHECK (("coach_tendency_career"."metric" in ('proe', 'neutral_sec_per_play') or "coach_tendency_career"."value" between 0 and 1) and ("coach_tendency_career"."metric" <> 'neutral_sec_per_play' or "coach_tendency_career"."value" >= 0)),
	CONSTRAINT "coach_tendency_career_seasons_check" CHECK ("coach_tendency_career"."seasons" >= 1 and "coach_tendency_career"."first_season" <= "coach_tendency_career"."last_season" and "coach_tendency_career"."sample" >= 1)
);
--> statement-breakpoint
CREATE TABLE "coach_tendency_fantasy_link" (
	"metric" text NOT NULL,
	"target" text NOT NULL,
	"horizon" text NOT NULL,
	"n" integer NOT NULL,
	"n_seasons" integer NOT NULL,
	"r" double precision,
	"ci_low" double precision,
	"ci_high" double precision,
	"x_sd" double precision,
	"y_per_x_sd" double precision,
	CONSTRAINT "coach_tendency_fantasy_link_metric_target_horizon_pk" PRIMARY KEY("metric","target","horizon"),
	CONSTRAINT "coach_tendency_fantasy_link_metric_check" CHECK ("coach_tendency_fantasy_link"."metric" in ('neutral_pass_rate', 'early_down_pass_rate', 'proe', 'neutral_sec_per_play', 'no_huddle_rate', 'shotgun_rate', 'fourth_go_rate', 'fourth_short_go_rate')),
	CONSTRAINT "coach_tendency_fantasy_link_target_check" CHECK ("coach_tendency_fantasy_link"."target" in ('targets_per_game', 'recv_ppr_per_game') and "coach_tendency_fantasy_link"."horizon" in ('same_season', 'next_season')),
	CONSTRAINT "coach_tendency_fantasy_link_r_check" CHECK ("coach_tendency_fantasy_link"."r" between -1 and 1 and "coach_tendency_fantasy_link"."ci_low" <= "coach_tendency_fantasy_link"."ci_high" and "coach_tendency_fantasy_link"."x_sd" >= 0),
	CONSTRAINT "coach_tendency_fantasy_link_counts_check" CHECK ("coach_tendency_fantasy_link"."n" >= 0 and "coach_tendency_fantasy_link"."n_seasons" >= 0)
);
--> statement-breakpoint
CREATE TABLE "coach_tendency_persistence" (
	"metric" text NOT NULL,
	"comparison" text NOT NULL,
	"n_pairs" integer NOT NULL,
	"n_seasons" integer NOT NULL,
	"first_season" integer,
	"last_season" integer,
	"r" double precision,
	"ci_low" double precision,
	"ci_high" double precision,
	CONSTRAINT "coach_tendency_persistence_metric_comparison_pk" PRIMARY KEY("metric","comparison"),
	CONSTRAINT "coach_tendency_persistence_metric_check" CHECK ("coach_tendency_persistence"."metric" in ('neutral_pass_rate', 'early_down_pass_rate', 'proe', 'neutral_sec_per_play', 'no_huddle_rate', 'shotgun_rate', 'fourth_go_rate', 'fourth_short_go_rate')),
	CONSTRAINT "coach_tendency_persistence_comparison_check" CHECK ("coach_tendency_persistence"."comparison" in ('same_coach_same_team', 'same_coach_new_team', 'new_coach_same_team')),
	CONSTRAINT "coach_tendency_persistence_r_check" CHECK ("coach_tendency_persistence"."r" between -1 and 1 and "coach_tendency_persistence"."ci_low" <= "coach_tendency_persistence"."ci_high"),
	CONSTRAINT "coach_tendency_persistence_counts_check" CHECK ("coach_tendency_persistence"."n_pairs" >= 0 and "coach_tendency_persistence"."n_seasons" >= 0)
);
--> statement-breakpoint
CREATE TABLE "coach_tendency_season" (
	"coach_id" text NOT NULL,
	"team" text NOT NULL,
	"season" integer NOT NULL,
	"metric" text NOT NULL,
	"is_current" boolean NOT NULL,
	"through_week" integer NOT NULL,
	"games" integer NOT NULL,
	"plays" integer NOT NULL,
	"value" double precision NOT NULL,
	"sample" integer NOT NULL,
	"league_avg" double precision NOT NULL,
	"percentile" double precision,
	CONSTRAINT "coach_tendency_season_coach_id_team_season_metric_pk" PRIMARY KEY("coach_id","team","season","metric"),
	CONSTRAINT "coach_tendency_season_metric_check" CHECK ("coach_tendency_season"."metric" in ('neutral_pass_rate', 'early_down_pass_rate', 'proe', 'neutral_sec_per_play', 'no_huddle_rate', 'shotgun_rate', 'fourth_go_rate', 'fourth_short_go_rate')),
	CONSTRAINT "coach_tendency_season_value_check" CHECK (("coach_tendency_season"."metric" in ('proe', 'neutral_sec_per_play') or "coach_tendency_season"."value" between 0 and 1) and ("coach_tendency_season"."metric" <> 'neutral_sec_per_play' or "coach_tendency_season"."value" >= 0)),
	CONSTRAINT "coach_tendency_season_percentile_check" CHECK ("coach_tendency_season"."percentile" between 0 and 100),
	CONSTRAINT "coach_tendency_season_counts_check" CHECK ("coach_tendency_season"."season" >= 1999 and "coach_tendency_season"."through_week" between 1 and 22 and "coach_tendency_season"."games" >= 1 and "coach_tendency_season"."plays" >= 0 and "coach_tendency_season"."sample" >= 1)
);
--> statement-breakpoint
ALTER TABLE "coach_tendency_career" ADD CONSTRAINT "coach_tendency_career_coach_id_dim_coach_coach_id_fk" FOREIGN KEY ("coach_id") REFERENCES "public"."dim_coach"("coach_id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "coach_tendency_season" ADD CONSTRAINT "coach_tendency_season_coach_id_dim_coach_coach_id_fk" FOREIGN KEY ("coach_id") REFERENCES "public"."dim_coach"("coach_id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "coach_tendency_season" ADD CONSTRAINT "coach_tendency_season_team_dim_team_team_abbr_fk" FOREIGN KEY ("team") REFERENCES "public"."dim_team"("team_abbr") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
CREATE INDEX "coach_tendency_season_season_idx" ON "coach_tendency_season" USING btree ("season","metric");