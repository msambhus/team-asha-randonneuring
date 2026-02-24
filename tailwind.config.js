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
          DEFAULT: '#ff6b00',
          dark: '#d45500',
        },
        accent: {
          DEFAULT: '#1e40af',
          light: '#3b82f6',
          red: '#dc2626',
        },
        bg: '#fefbf6',
        'card-bg': '#ffffff',
        text: {
          DEFAULT: '#2d3748',
          light: '#718096',
        },
        border: '#fed7aa',
        success: '#16a34a',
        warning: '#d69e2e',
        danger: '#dc2626',
      },
    },
  },
  plugins: [],
}
