/* The header's line on a chart page this person may not edit (core
 * accessMode / viewOnlyText). A phone's header is one 48px row, so there it
 * says just "View only", and the whole line is the title. "Sign in" shows
 * only when signing in could change the answer; the page then loads again,
 * so the boot asks access.json once more, with the new token. */
import { viewOnlyText } from "../core/index.mjs";
import { signInNow } from "../runtime/auth.mjs";

export function ViewOnly({ why }) {
  const text = viewOnlyText(why);
  const signIn = async () => {
    try { await signInNow(); location.reload(); } catch { /* the popup was shut */ }
  };
  return (
    <>
      <span id="viewOnly" className="pill" title={text}>
        <span className="long">{text}</span><span className="short">View only</span>
      </span>
      {why.reason === "signin"
        ? <button id="viewOnlySignIn" onClick={signIn}>Sign in</button> : null}
    </>
  );
}
