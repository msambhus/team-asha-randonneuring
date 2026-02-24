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
          DEFAULT: '#1e3a8a',
          dark: '#1e293b',
        },
        accent: {
          DEFAULT: '#ff6b00',
          light: '#ff8c42',
          red: '#dc2626',
        },
        bg: '#f8fafc',
        'card-bg': '#ffffff',
        text: {
          DEFAULT: '#2d3748',
          light: '#718096',
        },
        border: '#e2e8f0',
        success: '#16a34a',
        warning: '#d69e2e',
        danger: '#dc2626',
      },
    },
  },
  plugins: [],
}
