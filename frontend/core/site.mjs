/* Which site this page belongs to (service/site.py), as the server hands it
 * over in window.SITE: its name, the pages it leaves out of the nav, and
 * whether anyone but its operator can change anything. Granite's site sets
 * nothing, and everything here then answers as it always has.
 *
 * Plain ESM with no React and no DOM; see tests/test_viewer_js.py. */

const GRANITE_NAME = "curling.dimmit.net";

/* The site the page was served for, or null. */
export function currentSite() {
  return globalThis.window?.SITE ?? null;
}

/* `links` ([href, text, ...] pairs) without the pages this site leaves out. */
export function visibleLinks(links, site) {
  const hidden = new Set(site?.nav_hidden ?? []);
  return links.filter(([href]) => !hidden.has(href));
}

export function siteName(site) {
  return site?.name || GRANITE_NAME;
}

/* Whether this site is look-only for everyone without the admin token. */
export function siteReadOnly(site) {
  return !!site?.read_only;
}
