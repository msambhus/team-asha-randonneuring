/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./templates/**/*.html",
    "./templates/**/*.jinja2",
  ],
  theme: {
    extend: {
      colors: {
        primary: {
          DEFAULT: '#1a365d',
          dark: '#0f2744',
        },
        accent: {
          DEFAULT: '#ff6b00',
          light: '#ff8c42',
          red: '#e53e3e',
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
