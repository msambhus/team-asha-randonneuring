# iOS App - Quick Start Guide

Get the Team Asha Randonneuring iOS app running in 5 minutes!

## Prerequisites

- macOS with Xcode 15.0+ installed
- iOS Simulator or physical iPhone (iOS 16.0+)
- No additional dependencies needed!

## Option 1: Open Existing Xcode Project (Recommended)

If an `.xcodeproj` file exists:

```bash
cd ios
open TeamAshaRandonneuring.xcodeproj
```

Then:
1. Select a simulator (iPhone 15 Pro recommended)
2. Press Cmd+R to build and run
3. Wait for app to launch in simulator

## Option 2: Create New Xcode Project

If no `.xcodeproj` exists yet:

### Step 1: Create Project in Xcode

1. Open Xcode
2. File → New → Project
3. Choose "iOS" → "App"
4. Settings:
   - Product Name: `TeamAshaRandonneuring`
   - Team: (your Apple ID)
   - Organization Identifier: `com.teamasha` (or your own)
   - Interface: **SwiftUI**
   - Language: **Swift**
   - Storage: None
   - Include Tests: ✓ (optional)
5. Save in `ios/` directory

### Step 2: Add Source Files

1. Delete the default `ContentView.swift` that Xcode created
2. In Xcode's Project Navigator, right-click on "TeamAshaRandonneuring"
3. Select "Add Files to TeamAshaRandonneuring..."
4. Navigate to `ios/TeamAshaRandonneuring/` folder
5. Select all folders: App, Models, Services, ViewModels, Views
6. Ensure "Copy items if needed" is **checked**
7. Ensure "Create groups" is selected
8. Click "Add"

### Step 3: Replace Info.plist

1. Delete the auto-generated Info.plist
2. Add the Info.plist from `ios/TeamAshaRandonneuring/Info.plist`

### Step 4: Build & Run

1. Select target device (iPhone 15 Pro simulator)
2. Press Cmd+R to build and run
3. Fix any build errors (usually missing imports or file references)

## Using the App

### Demo Login

1. App launches to Welcome screen
2. Tap "Demo Login"
3. Enter any email (e.g., `demo@test.com`)
4. Tap "Login"
5. You're in!

### Explore Features

**Brevets Tab**:
- Browse upcoming randonneuring events
- Tap an event to see details
- Click links to view routes

**Riders Tab**:
- View season leaderboard
- Use season picker (top right) to change seasons
- Tap a rider to see their profile

**Profile Tab**:
- View your stats (if you have an actual account)
- Connect Strava (placeholder UI)
- See Eddington Number
- Gear icon → Settings → Sign Out

## Development Configuration

### Use Local Backend

To test against local Flask server instead of production:

1. Start local Flask server:
   ```bash
   cd ../
   flask run -p 5001
   ```

2. In Xcode, edit `TeamAshaRandonneuringApp.swift`:
   ```swift
   .onAppear {
       // Use local dev server
       apiClient.configure(baseURL: "http://localhost:5001")
   }
   ```

3. Build and run

**Note**: Simulator can access localhost directly!

### Enable Network Debugging

To see all API requests:

1. Open `APIClient.swift`
2. Add print statements:
   ```swift
   private func request<T: Decodable>(...) async throws -> T {
       print("📡 API Request: \(method) \(url)")
       // ... existing code
       print("✅ API Response: \(httpResponse.statusCode)")
   }
   ```

3. Check Xcode console for logs

## Troubleshooting

### Build Errors

**"No such module 'SwiftUI'"**
- Deployment target must be iOS 16.0+
- Check in Project Settings → General → Deployment Info

**"Cannot find 'APIClient' in scope"**
- Ensure all files are added to target
- Check Project Navigator - files should not be grayed out
- Right-click file → Target Membership → check app target

**"Ambiguous use of 'init'"**
- Clean build folder: Cmd+Shift+K
- Rebuild: Cmd+B

### Runtime Issues

**"Failed to load data"**
- Check API endpoint URL
- Verify backend is running
- Check console for detailed error
- Test API with curl:
  ```bash
  curl https://team-asha-randonneuring.vercel.app/api/brevets/upcoming
  ```

**App crashes on launch**
- Check for force-unwrapping (!)
- Review console crash logs
- Ensure all models match API response

**Data not showing**
- Use "Demo Login" (Google Sign-In not yet integrated)
- Check loading state (should see spinner)
- Refresh with pull-down gesture

### Simulator Issues

**App not appearing**
- Wait for build to complete
- Check "Run" section in Xcode for errors
- Try different simulator (Device → Manage Devices)

**Slow performance**
- Simulator is slower than real device
- Use iPhone 15 Pro simulator (newer = faster)
- Close other apps
- Restart simulator

## Testing Checklist

Quick verification that everything works:

- [ ] App launches without crash
- [ ] Welcome screen shows
- [ ] Demo login works
- [ ] Tabs appear after login
- [ ] Brevets load in first tab
- [ ] Can tap brevet to see detail
- [ ] Riders leaderboard shows
- [ ] Can change season
- [ ] Profile tab shows user info
- [ ] Settings accessible (gear icon)
- [ ] Sign out returns to welcome screen

## Next Steps

Once app is running:

1. **Explore codebase**:
   - Read `ios/README.md` for architecture overview
   - Read `ios/DESIGN.md` for design decisions
   - Browse `Views/` folder for UI code

2. **Make changes**:
   - Edit any `.swift` file
   - Save (Cmd+S)
   - Run (Cmd+R)
   - See changes in simulator

3. **Add features**:
   - Follow MVVM pattern
   - Add models, then ViewModel, then View
   - Test thoroughly
   - Update documentation

4. **Real authentication**:
   - See README.md → Authentication section
   - Requires GoogleSignIn SDK integration

## Common Customizations

### Change App Icon

1. Create icon set in Assets.xcassets/AppIcon
2. Add images in required sizes
3. Rebuild

### Change Base URL

Edit `APIClient.swift`:
```swift
private var baseURL = "https://your-custom-domain.com"
```

### Add New Tab

Edit `ContentView.swift`:
```swift
YourNewView()
    .tabItem {
        Label("Name", systemImage: "icon.name")
    }
    .tag(3)
```

### Change Colors

Edit any view file:
```swift
.foregroundColor(.red)  // System color
.foregroundColor(Color(red: 0.5, green: 0.5, blue: 0.5))  // Custom
```

## Support

**Build issues**: Check Xcode console for specific errors
**API issues**: Test endpoints with curl or Postman
**UI issues**: Try different simulators
**Still stuck**: Open GitHub issue with:
- Xcode version
- iOS version
- Error message
- Steps to reproduce

---

**Ready to ride! 🚴**
