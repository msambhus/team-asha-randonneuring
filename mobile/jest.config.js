// mobile/jest.config.js — jest-expo preset with ESM transform allowance.
process.env.RNTL_SKIP_DEPS_CHECK = '1';

const { join } = require('path');

module.exports = {
  preset: 'jest-expo',
  setupFilesAfterEnv: [join(__dirname, 'jest.setup.ts')],
  transformIgnorePatterns: [
    'node_modules/(?!(' +
      [
        '@expo',
        'expo',
        'expo-[\\w-]+',
        'react-native',
        '@react-native',
        '@react-native-google-signin',
        'react-native-maps',
        'react-native-gesture-handler',
        'react-native-safe-area-context',
        'react-native-screens',
        '@tanstack',
      ].join('|') +
      ')/)',
  ],
  testRegex: '\\.(test|spec)\\.(ts|tsx)$',
};
