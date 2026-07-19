/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./templates/**/*.html",
    "./templates/**/*.jinja2",
    // BrevetHub reuses this one canonical design system: scan its templates too
    // so BH-only utility classes compile into the shared output.css. Additive
    // only — cannot alter any rule the Team Asha templates already produce.
    "./brevethub/templates/**/*.html",
    "./brevethub/templates/**/*.jinja2",
  ],
  theme: {
    extend: {
      colors: {
        primary: {
          DEFAULT: '#1a365d',
          dark: '#2a4a7f',
        },
        accent: {
          DEFAULT: '#e53e3e',
          light: '#fc8181',
        },
        bg: '#f7fafc',
        'card-bg': '#ffffff',
        text: {
          DEFAULT: '#2d3748',
          light: '#718096',
        },
        border: '#e2e8f0',
        success: '#38a169',
        warning: '#d69e2e',
        danger: '#e53e3e',
      },
    },
  },
  plugins: [],
}
