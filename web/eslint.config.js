/**
 * Lint rules for the web app (CLAUDE.md rule 9).
 *
 * Type-aware linting is deliberately not turned on: `vue-tsc --build` already
 * typechecks every file in strict mode, and running the same type information
 * twice doubles the slowest part of the check for no finding it would not have
 * made. What is left is the things a typechecker does not say — unused code,
 * template mistakes, and the handful of Vue rules that catch a bug rather than
 * a preference.
 */
import js from '@eslint/js'
import vue from 'eslint-plugin-vue'
import globals from 'globals'
import ts from 'typescript-eslint'

export default ts.config(
  { ignores: ['dist/**', 'node_modules/**'] },
  js.configs.recommended,
  ...ts.configs.recommended,
  ...vue.configs['flat/recommended'],
  {
    languageOptions: {
      globals: { ...globals.browser },
    },
  },
  {
    files: ['**/*.vue'],
    languageOptions: {
      parserOptions: { parser: ts.parser },
    },
  },
  {
    rules: {
      // A component file named `Dashboard.vue` is fine here; the folder says
      // what it is, and `DashboardView` in every import does not read better.
      'vue/multi-word-component-names': 'off',
      // Formatting is not lint's job.
      'vue/max-attributes-per-line': 'off',
      'vue/singleline-html-element-content-newline': 'off',
      'vue/html-self-closing': 'off',
      'vue/html-indent': 'off',
      'vue/html-closing-bracket-newline': 'off',
      'vue/attributes-order': 'off',
      // Optionality is in the TypeScript prop types; a default is a separate
      // decision and `withDefaults` is where it gets made.
      'vue/require-default-prop': 'off',
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
    },
  },
)
