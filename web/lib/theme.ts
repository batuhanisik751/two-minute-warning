// Light/dark: the site follows the system setting; the toggle stores an explicit choice
// under THEME_KEY and puts it on <html data-theme>. The inline script runs in <head> before
// the first paint (Next's guide "How to prevent flash before hydration", Themes), so a
// stored choice never flashes the other theme on load.
export const THEME_KEY = "twm-theme";

export const THEME_SCRIPT = `(function(){try{var t=localStorage.getItem(${JSON.stringify(
  THEME_KEY,
)});if(t==="light"||t==="dark")document.documentElement.setAttribute("data-theme",t)}catch(e){}})()`;
