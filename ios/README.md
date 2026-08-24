# Team Asha Randonneuring - iOS App

Native iOS app for Team Asha Randonneuring, built with SwiftUI.

## Features

### 🚴 Core Features

- **Upcoming Brevets Calendar** - Browse upcoming randonneuring events with dates, distances, and locations
- **Season Leaderboard** - View rider rankings for current and past seasons
- **Rider Profiles** - Detailed rider stats, career achievements, and season history
- **Strava Integration** - Connect Strava to track activities and Eddington Number
- **Personal Profile** - Manage your profile, sync Strava, and view your achievements

### 📊 Key Screens

1. **Brevets Tab**
   - List of upcoming brevets grouped by month
   - Distance badges with color coding (200km, 400km, 600km, 1000km+)
   - Event details: date, location, elevation, RUSA/RideWithGPS links
   - Direct links to route maps

2. **Riders Tab**
   - Season leaderboard with rankings
   - Rider search and filtering
   - Super Randonneur badges
   - Season selector dropdown
   - Tap to view detailed rider profile

3. **Profile Tab**
   - Personal stats and achievements
   - Strava connection status
   - Eddington Number display with progress
   - Recent activity feed
   - Season history
   - Settings and sign out

### 💪 Eddington Number

Beautiful display of cycling achievement metrics:
- Current Eddington Number (miles and km)
- Achievement badge level (Getting Started → Legendary)
- Visual progress bar to next milestone
- Days completed vs. days needed

### 🎨 Design Highlights

- Native iOS design with SF Symbols
- Light and dark mode support
- Pull-to-refresh on all lists
- Smooth async/await data loading
- Error states with retry
- Empty states with helpful messages
- Responsive layouts for iPhone and iPad

## Tech Stack

- **Language**: Swift 5.9+
- **UI Framework**: SwiftUI
- **Architecture**: MVVM (Model-View-ViewModel)
- **Networking**: async/await with URLSession
- **State Management**: @StateObject, @EnvironmentObject
- **Backend API**: Flask REST API (team-asha-randonneuring.vercel.app)

## Project Structure

```
ios/TeamAshaRandonneuring/
├── App/
│   ├── TeamAshaRandonneuringApp.swift    # App entry point
│   └── ContentView.swift                  # Root view with tab navigation
│
├── Models/
│   ├── Rider.swift                        # Rider and profile models
│   ├── Brevet.swift                       # Brevet event models
│   ├── Strava.swift                       # Strava connection & activity models
│   └── Season.swift                       # Season and leaderboard models
│
├── Services/
│   ├── APIClient.swift                    # REST API client
│   └── AuthService.swift                  # Authentication service
│
├── ViewModels/
│   ├── BrevetsViewModel.swift             # Brevets list state
│   ├── RidersViewModel.swift              # Leaderboard state
│   └── ProfileViewModel.swift             # Profile state
│
├── Views/
│   ├── WelcomeView.swift                  # Login screen
│   │
│   ├── Brevets/
│   │   ├── BrevetsListView.swift          # Brevets calendar
│   │   └── BrevetDetailView.swift         # Brevet details
│   │
│   ├── Riders/
│   │   ├── RidersListView.swift           # Season leaderboard
│   │   └── RiderDetailView.swift          # Rider profile
│   │
│   ├── Profile/
│   │   └── MyProfileView.swift            # User profile
│   │
│   ├── Strava/
│   │   └── StravaSection.swift            # Strava connection UI
│   │
│   └── Components/
│       ├── EddingtonCard.swift            # Eddington display
│       ├── ActivityRow.swift              # Strava activity row
│       └── ErrorView.swift                # Error & empty states
│
└── Resources/
    └── Info.plist                         # App configuration
```

## Requirements

- **iOS**: 16.0+
- **Xcode**: 15.0+
- **Swift**: 5.9+

## Setup Instructions

### 1. Open in Xcode

```bash
cd ios
open TeamAshaRandonneuring.xcodeproj
```

If you don't have an Xcode project file yet, you'll need to create one:

1. Open Xcode
2. Create New Project → iOS App
3. Product Name: `TeamAshaRandonneuring`
4. Organization Identifier: `com.teamasha` (or your own)
5. Interface: SwiftUI
6. Language: Swift
7. Save in the `ios/` directory

### 2. Add Source Files

Add all Swift files from the directory structure above to your Xcode project:

1. Right-click on project in Navigator
2. Add Files to "TeamAshaRandonneuring"
3. Select all `.swift` files
4. Ensure "Copy items if needed" is checked

### 3. Configure API Endpoint

The app points to production by default: `https://team-asha-randonneuring.vercel.app`

To use a local development server:

```swift
// In TeamAshaRandonneuringApp.swift
apiClient.configure(baseURL: "http://localhost:5001")
```

### 4. Build & Run

1. Select a simulator or connected device
2. Press Cmd+R to build and run
3. Use Demo Login for testing (Google Sign-In not yet integrated)

## Authentication

### Current: Demo Login

The app includes a demo login for development:

1. Tap "Demo Login" on welcome screen
2. Enter any email address
3. Login without backend verification

**Note**: This is for development only and should be removed in production.

### Future: Google Sign-In Integration

To add production Google authentication:

1. Install GoogleSignIn SDK:
   ```ruby
   pod 'GoogleSignIn'
   ```

2. Configure OAuth client ID in Google Cloud Console

3. Add URL scheme to Info.plist:
   ```xml
   <key>CFBundleURLTypes</key>
   <array>
     <dict>
       <key>CFBundleURLSchemes</key>
       <array>
         <string>com.googleusercontent.apps.YOUR_CLIENT_ID</string>
       </array>
     </dict>
   </array>
   ```

4. Implement `signInWithGoogle()` in `AuthService.swift`

5. Update welcome screen to use real OAuth flow

## API Endpoints Used

The app consumes these backend endpoints:

- `GET /api/brevets/upcoming` - Upcoming brevets
- `GET /api/riders` - Current season leaderboard
- `GET /api/riders/season/:id` - Specific season leaderboard
- `GET /riders/:id` - Rider profile
- `GET /api/profile/me` - Current user profile (authenticated)
- `POST /strava/sync` - Sync Strava activities (authenticated)
- `GET /api/strava/activities/:id?days=N` - Rider's activities
- `GET /api/seasons` - All seasons

## State Management

The app uses SwiftUI's native state management:

- **@StateObject**: For view model lifecycle
- **@EnvironmentObject**: For shared services (AuthService, APIClient)
- **@Published**: For observable properties in ViewModels
- **@State**: For local view state

## Error Handling

All API calls use Swift's async/await with proper error handling:

```swift
do {
    brevets = try await apiClient.fetchUpcomingBrevets()
} catch {
    errorMessage = error.localizedDescription
}
```

Error UI includes:
- ErrorView with retry button
- EmptyStateView for no data
- Loading indicators
- Toast-like messages for success/failure

## Design Decisions

### Why SwiftUI?

- Modern, declarative UI framework
- Native iOS performance
- Built-in dark mode support
- Less code than UIKit
- Future-proof (Apple's recommended approach)

### Why MVVM?

- Clear separation of concerns
- Easy to test
- Works well with SwiftUI's @Published
- Standard pattern for SwiftUI apps

### Why No Dependencies?

- Uses native URLSession (no Alamofire)
- Uses native async/await (no Combine)
- Smaller app size
- Faster build times
- No dependency version conflicts

### Why Not SwiftData?

- Backend is source of truth
- No offline mode needed
- Simpler architecture
- API-first approach

## Testing

### Manual Testing Checklist

- [ ] App launches without crash
- [ ] Demo login works
- [ ] Brevets load and display
- [ ] Brevets grouped by month
- [ ] Brevet detail shows correct info
- [ ] External links open correctly
- [ ] Riders leaderboard loads
- [ ] Season selector works
- [ ] Rider detail shows profile
- [ ] Profile tab shows user data
- [ ] Strava section displays correctly
- [ ] Eddington card shows properly
- [ ] Pull-to-refresh works on all tabs
- [ ] Sign out clears state
- [ ] Dark mode works
- [ ] iPad layout is responsive

### Future: Unit Tests

Add tests for:
- APIClient methods
- ViewModel business logic
- Model decoding
- AuthService state management

## Future Enhancements

### Near Term
- [ ] Real Google Sign-In integration
- [ ] Strava OAuth connection flow
- [ ] Push notifications for upcoming brevets
- [ ] Offline mode for brevets list
- [ ] Search and filter riders
- [ ] Brevet sign-up flow

### Long Term
- [ ] Apple Watch app
- [ ] Home screen widgets
- [ ] Live Activities for active brevets
- [ ] Social features (comments, photos)
- [ ] Route navigation integration
- [ ] Training plan generation
- [ ] Personal stats dashboard

## Contributing

When adding new features:

1. Follow MVVM pattern
2. Use async/await for networking
3. Add proper error handling
4. Include loading states
5. Support dark mode
6. Test on iPhone and iPad
7. Update this README

## Troubleshooting

### App won't build
- Check Xcode version (15.0+)
- Clean build folder (Cmd+Shift+K)
- Delete DerivedData
- Restart Xcode

### API calls fail
- Check network connection
- Verify API endpoint URL
- Check console for error details
- Test endpoint with curl/Postman

### Authentication issues
- Clear app data (delete and reinstall)
- Check UserDefaults for corrupted data
- Verify demo login is enabled

## License

Private project for Team Asha Randonneuring.

## Support

For issues or questions:
- Open issue on GitHub
- Contact team maintainers

---

**Built with ❤️ for Team Asha Randonneuring**
