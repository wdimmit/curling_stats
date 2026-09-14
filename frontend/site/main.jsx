/* One bundle for every page on the site.
 *
 * All five together are ~31 KB against React's ~190, so serving all of them
 * everywhere costs almost nothing and means React is downloaded once and
 * cached across the whole site -- which five separate bundles would not do.
 *
 * The page is chosen by body[data-page], never by reading location.pathname:
 * the status page is served both at `/c/{slug}/` and, once a run is ready, is
 * replaced by the viewer at the same URL. The document says what it is.
 */
import { createRoot } from "react-dom/client";
import { Games } from "./Games.jsx";
import { Join } from "./Join.jsx";
import { Mine } from "./Mine.jsx";
import { Status } from "./Status.jsx";
import { Submit } from "./Submit.jsx";

const PAGES = { submit: Submit, games: Games, mine: Mine, join: Join, status: Status };

const which = document.body.dataset.page;
const Page = PAGES[which];
const root = document.getElementById("root");

if (!Page || !root) {
  console.error(`no page for data-page=${JSON.stringify(which)}`);
} else {
  createRoot(root).render(<Page />);
}
