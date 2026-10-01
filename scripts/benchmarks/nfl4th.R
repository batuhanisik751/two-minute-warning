#!/usr/bin/env Rscript
# nfl4th benchmark (Two-Minute Warning step G5; PROJECT_SPEC 8.4 "Benchmark").
#
# Runs the nfl4th R package's fourth-down calculator on fourth-down states that WE export
# (src/twm/modules/decisions/benchmark.py) and writes nfl4th's WP for go / field goal / punt and
# its recommendation, one row per play key (game_id, play_id).
#
# Verified against the INSTALLED nfl4th 1.0.7 source and help (add_4th_probs, prepare_df,
# get_fg_wp, get_games_file, cached_model, .onLoad), not from memory:
#   - nfl4th::add_4th_probs(df) takes a data frame (its help lists home_team, away_team, posteam,
#     type, season, qtr, quarter_seconds_remaining, ydstogo, yardline_100, score_differential,
#     home_opening_kickoff, posteam/defteam_timeouts_remaining; an optional `runoff`, default 0);
#     we never call load_4th_pbp() (it downloads play-by-play).
#   - prepare_df() joins a games table (lines, roof, eras) on home_team, away_team, type, season.
#     nfl4th builds it with nflreadr::load_schedules() (a download) unless
#     options(nfl4th.keep_games = TRUE) and a cached games_nfl4th.rds exist: we write that file
#     from OUR warehouse (games CSV), with get_games_file()'s own transformations copied below.
#   - Models: the field-goal model (fg_model), the punt table and the 2-point model ship inside
#     the package; the WP model (wp_model.rds) and the conversion model (fd_model.rds) are
#     DOWNLOADED from GitHub on first use and cached in tools::R_user_dir("nfl4th", "cache").
#     The package does not document the seasons its models were trained on (help + NEWS read);
#     they were fitted on nflfastR data that very likely includes the benchmarked seasons, so the
#     benchmark is in-sample for nfl4th (PROJECT_SPEC 6.3) and out-of-sample for us.
#
# No network, ever: the cache is redirected to --cache (R_USER_CACHE_DIR, a project folder),
# nfl4th's on-load DNS check is answered "offline", and every function that would download is
# replaced by an error. Without wp_model.rds + fd_model.rds in <cache>/R/nfl4th/ the script
# computes only what ships with the package (field-goal make probability) and writes
# nfl4th_status = "fg_only"; with them, the full calculator ("full").
#
# Usage: Rscript nfl4th.R --states states.csv --games games.csv --out out.csv --cache DIR
# Exit codes: 0 written; 2 bad arguments; 3 nfl4th not installed; 4 input problem.

args <- commandArgs(trailingOnly = TRUE)
opt <- list()
if (length(args) %% 2 == 0 && length(args) > 0) {
  for (i in seq(1, length(args), by = 2)) opt[[sub("^--", "", args[i])]] <- args[i + 1]
}
if (!all(c("states", "games", "out", "cache") %in% names(opt))) {
  message("usage: Rscript nfl4th.R --states S.csv --games G.csv --out O.csv --cache DIR")
  quit(status = 2)
}
if (!nzchar(system.file(package = "nfl4th"))) {  # checked without loading it
  message("nfl4th is not installed in this R (", R.home(), ")")
  quit(status = 3)
}

# --- offline setup (before nfl4th is loaded) -------------------------------------------------
dir.create(opt$cache, recursive = TRUE, showWarnings = FALSE)
Sys.setenv(R_USER_CACHE_DIR = normalizePath(opt$cache))  # tools::R_user_dir() honours it
options(nfl4th.keep_games = TRUE)  # .onLoad must not delete our games file
# .onLoad asks DNS whether github.com is reachable (curl::nslookup): answer "offline".
utils::assignInNamespace("nslookup", function(...) NULL, ns = "curl")
blocked <- function(...) stop("nfl4th tried to download; blocked by nfl4th.R", call. = FALSE)
invisible(suppressMessages(loadNamespace("nfl4th")))
utils::assignInNamespace("raw_rds_from_url", blocked, ns = "nfl4th")  # wp / fd models
utils::assignInNamespace("get_games_file", blocked, ns = "nfl4th")  # schedules
utils::assignInNamespace("load_schedules", blocked, ns = "nflreadr")
utils::assignInNamespace("load_pbp", blocked, ns = "nflreadr")
if (nfl4th:::probably_cran()) stop("nfl4th thinks it runs on CRAN and would bypass its cache")
nfl4th_version <- as.character(utils::packageVersion("nfl4th"))
`%>%` <- dplyr::`%>%`

# --- the games table nfl4th joins, from OUR warehouse ------------------------------------------
# Same transformations as nfl4th:::get_games_file() (1.0.7), applied to our games CSV
# (game_id, season, game_type, week, home_team, away_team, roof, spread_line, total_line)
# instead of nflreadr::load_schedules(). spread_line: home team's expected margin (nflverse).
g <- utils::read.csv(opt$games, stringsAsFactors = FALSE)
games <- g %>%
  dplyr::filter(season > 2013) %>%
  dplyr::mutate(
    type = dplyr::if_else(game_type == "REG", "reg", "post"),
    model_roof = dplyr::case_when(
      roof == "open" | roof == "closed" | is.na(roof) ~ "retractable",
      roof == "retractable" ~ "retractable", roof == "dome" ~ "dome", TRUE ~ "outdoors"),
    era0 = 0, era1 = 0, era2 = 0,
    era3 = dplyr::if_else(season > 2013 & season <= 2017, 1, 0),
    era4 = dplyr::if_else(season > 2017, 1, 0),
    fg_roof = dplyr::case_when(roof == "outdoors" ~ 1, TRUE ~ 0),
    fg_era = dplyr::case_when(season >= 2020 ~ 1, TRUE ~ 0),
    # all four levels, as in nfl4th's full 2014+ schedule (a subset of seasons may lack some)
    fg_model_roof = factor(paste0(fg_roof, fg_era), levels = c("00", "01", "10", "11")),
    home_total = (total_line + spread_line) / 2, away_total = (total_line - spread_line) / 2,
    retractable = dplyr::if_else(model_roof == "retractable", 1, 0),
    dome = dplyr::if_else(model_roof == "dome", 1, 0),
    outdoors = dplyr::if_else(model_roof == "outdoors", 1, 0),
    roof = model_roof, espn = NA_character_) %>%
  dplyr::mutate_at(dplyr::vars("home_team", "away_team"), nfl4th:::team_name_fn) %>%
  dplyr::select(game_id, season, type, week, away_team, home_team, espn, fg_model_roof,
                model_roof, roof, era0, era1, era2, era3, era4, home_total, away_total,
                total_line, spread_line, retractable, dome, outdoors)
if (anyNA(games$spread_line) || anyNA(games$total_line)) {
  message("games CSV: missing spread_line / total_line")
  quit(status = 4)
}
saveRDS(games, nfl4th:::nfl4th_games_path())  # read back by nfl4th:::.games_nfl4th()

# --- the fourth-down states --------------------------------------------------------------------
need <- c("game_id", "play_id", "home_team", "away_team", "posteam", "type", "season", "qtr",
          "quarter_seconds_remaining", "ydstogo", "yardline_100", "score_differential",
          "home_opening_kickoff", "posteam_timeouts_remaining", "defteam_timeouts_remaining")
s <- utils::read.csv(opt$states, stringsAsFactors = FALSE)
if (!all(need %in% names(s)) || anyNA(s[need]) || nrow(s) == 0) {
  message("states CSV: needs non-missing ", paste(need, collapse = ", "))
  quit(status = 4)
}
s <- s[need]  # nothing else reaches nfl4th (no runoff column: nfl4th's default 0)
for (v in c("home_team", "away_team", "posteam")) s[[v]] <- nfl4th:::team_name_fn(s[[v]])
prep <- nfl4th:::prepare_df(s)  # the frame every nfl4th option function sees
unmatched <- sum(is.na(prep$spread_line))
if (unmatched > 0) {
  message(unmatched, " states found no game in the games table (home, away, type, season)")
  quit(status = 4)
}
if (nrow(prep) != nrow(s)) {
  message("a state matched more than one game in the games table")
  quit(status = 4)
}

# --- field-goal make probability: ships with the package ---------------------------------------
# The first lines of nfl4th:::get_fg_wp() (1.0.7), verbatim in effect: the bundled mgcv model,
# kicks from beyond the 40 scaled down from the 40-yard-line value, 0 from the 53 out.
fg_make <- function(df) {
  p <- as.numeric(mgcv::predict.bam(nfl4th:::fg_model, newdata = df, type = "response"))
  p40 <- as.numeric(mgcv::predict.bam(nfl4th:::fg_model, newdata = dplyr::mutate(df, yardline_100 = 40),
                                      type = "response"))
  p <- ifelse(df$yardline_100 > 40, (53 - df$yardline_100) / 13 * p40, p)
  ifelse(df$yardline_100 >= 53, 0, p)
}
out <- data.frame(game_id = s$game_id, play_id = s$play_id, nfl4th_version = nfl4th_version,
                  fg_make_prob_bundled = fg_make(prep))

# --- the full calculator, only when both downloaded models are already cached -----------------
have_models <- file.exists(nfl4th:::nfl4th_wpmodel_path()) &&
  file.exists(nfl4th:::nfl4th_fdmodel_path())
cols <- c("go_boost", "first_down_prob", "wp_fail", "wp_succeed", "go_wp", "fg_make_prob",
          "miss_fg_wp", "make_fg_wp", "fg_wp", "punt_wp")  # add_4th_probs' documented outputs
if (have_models) {
  res <- suppressMessages(nfl4th::add_4th_probs(s))
  if (nrow(res) != nrow(s) || any(res$play_id != s$play_id)) stop("add_4th_probs changed the rows")
  # self-check: our copy of the field-goal lines equals the package's own result
  d <- max(abs(res$fg_make_prob - out$fg_make_prob_bundled))
  if (!is.finite(d) || d > 1e-9) stop("fg_make_prob differs from nfl4th's own: ", d)
  for (v in cols) out[[v]] <- res[[v]]
  # recommendation = the option with the highest WP; punt_wp is NA where nfl4th has no punt
  # (inside the 31: its punt table starts there); ties go to the earlier of go, FG, punt
  w <- cbind(go = res$go_wp, field_goal = res$fg_wp, punt = res$punt_wp)
  out$nfl4th_recommended <- apply(w, 1, function(x) {
    if (all(is.na(x))) NA_character_ else names(x)[which.max(x)]
  })
  out$nfl4th_status <- "full"
} else {
  for (v in cols) out[[v]] <- NA_real_
  out$nfl4th_recommended <- NA_character_
  out$nfl4th_status <- "fg_only"
  message("nfl4th's WP and conversion models are not in ", dirname(nfl4th:::nfl4th_wpmodel_path()),
          " (they are downloads, never made here): wrote the field-goal make probability only")
}
utils::write.csv(out, opt$out, row.names = FALSE, na = "")
message("nfl4th ", nfl4th_version, ": ", nrow(out), " states -> ", opt$out, " (", out$nfl4th_status[1], ")")
