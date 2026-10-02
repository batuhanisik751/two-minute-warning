// Text that every page carries (PROJECT_SPEC 16), in a plain module so the tests can import
// it without React.
export const DISCLAIMER =
  "Two-Minute Warning is an unofficial, educational project. It is not affiliated with or endorsed by the NFL or ESPN. Predictions are probabilistic and frequently wrong. Not betting advice.";

/** The sources credited in the footer (components/SiteFooter.tsx says why each one). */
export const CREDITS = ["nflverse", "ffopportunity", "DynastyProcess", "FantasyPros", "Pro Football Reference", "Wikipedia"] as const;
