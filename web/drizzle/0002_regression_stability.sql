CREATE TABLE "regression_stability" (
	"line" integer PRIMARY KEY NOT NULL,
	"section" text NOT NULL,
	"seasons" text NOT NULL,
	"split" text NOT NULL,
	"position" text NOT NULL,
	"metric" text NOT NULL,
	"g" integer,
	"n" integer NOT NULL,
	"value" double precision NOT NULL,
	"lo" double precision,
	"hi" double precision,
	"var_signal" double precision,
	"var_noise" double precision,
	"prior_mean" double precision
);
