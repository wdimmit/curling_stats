/* Lint the frontend sources.
 *
 * This exists for one rule. esbuild bundles an undefined identifier without a
 * murmur, so `shot` used in a component it was never passed compiled clean and
 * rendered an empty page in a browser -- which is a slow and stupid way to
 * learn about a typo. no-undef catches it in about a second.
 *
 * The globals are listed by hand rather than pulled from a package. It is a
 * short list, everything on it is something this code genuinely uses, and an
 * unfamiliar name showing up as an error is the point.
 */
import reactHooks from "eslint-plugin-react-hooks";

const BROWSER = [
  "document", "window", "navigator", "location", "console", "fetch", "alert",
  "prompt", "print", "matchMedia", "getComputedStyle", "addEventListener",
  "removeEventListener", "dispatchEvent", "localStorage", "sessionStorage",
  "setTimeout", "clearTimeout", "setInterval", "clearInterval",
  "requestAnimationFrame", "cancelAnimationFrame",
  "Blob", "URL", "Headers", "DOMPoint", "MouseEvent", "Image", "FormData",
  "IntersectionObserver", "MutationObserver", "ResizeObserver",
];

export default [
  {
    files: ["**/*.{js,mjs,jsx}"],
    ignores: ["node_modules/**"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      parserOptions: { ecmaFeatures: { jsx: true } },
      globals: Object.fromEntries(BROWSER.map(n => [n, "readonly"])),
    },
    linterOptions: { reportUnusedDisableDirectives: "error" },
    rules: {
      "no-undef": "error",
      "no-unused-vars": ["error", { args: "none", varsIgnorePattern: "^_" }],
      "no-console": ["error", { allow: ["error", "warn"] }],
      eqeqeq: ["error", "always", { null: "ignore" }],
      "no-var": "error",
      "prefer-const": "error",
    },
  },

  /* build.mjs is a node script, not a page: it has a process and its whole
   * output is console.log. */
  {
    files: ["build.mjs"],
    languageOptions: { globals: { process: "readonly", console: "readonly" } },
    rules: { "no-console": "off" },
  },

  /* The core is imported by the Python test suite under bare node. Nothing in
   * it may touch the DOM -- that is the whole reason it can be tested at all
   * -- and the tests only find out by crashing. Here it is a lint error. */
  {
    files: ["core/**/*.mjs"],
    languageOptions: { globals: {} },
    rules: {
      "no-restricted-globals": ["error",
        ...["document", "window", "navigator", "location", "localStorage",
            "sessionStorage", "matchMedia", "fetch"].map(name => ({
          name,
          message: "frontend/core is imported under bare node by the Python "
                 + "tests; it must not touch the DOM or the network.",
        }))],
    },
  },

  /* YT is defined by the IFrame API script the player injects at runtime. */
  {
    files: ["runtime/player.mjs"],
    languageOptions: { globals: { YT: "readonly" } },
  },

  {
    files: ["viewer/**/*.{js,jsx}", "site/**/*.{js,jsx}"],
    plugins: { "react-hooks": reactHooks },
    rules: {
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "warn",
    },
  },
];
