import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

export default defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    // Same as the old .eslintrc: unused-vars errors, unused disable directives are not reported.
    linterOptions: { reportUnusedDisableDirectives: "off" },
    rules: {
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
      // eslint-plugin-react-hooks 7 (bundled by eslint-config-next 16) adds React Compiler
      // rules that the 15.x config did not enforce. They flag existing patterns (ref sync in
      // render, setState in effects, react-hook-form watch()). Adopting the Compiler is a
      // separate refactor, so these stay off to keep the lint gate where it was.
      "react-hooks/set-state-in-effect": "off",
      "react-hooks/refs": "off",
      "react-hooks/incompatible-library": "off",
    },
  },
  globalIgnores(["components/ui/**", "lib/api/generated/**", ".next/**", "out/**"]),
]);
