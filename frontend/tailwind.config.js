/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        'vine-indigo': '#9B81F5',
        'foundation-grey': '#1D1D1B',
        'clarity-white': '#FFFFFF',
        'vine-mint': '#B1F0DB',
        'vine-peach': '#FFBB90',
        'vine-mustard': '#F1F68E',
      },
      fontFamily: {
        heading: ['Poppins', 'sans-serif'],
        body: ['Poppins', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
