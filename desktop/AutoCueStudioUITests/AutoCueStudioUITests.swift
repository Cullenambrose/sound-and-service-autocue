import XCTest

final class AutoCueStudioUITests: XCTestCase {
    private var syntheticDataPath: String!
    private var application: XCUIApplication?

    override func setUpWithError() throws {
        continueAfterFailure = false
        let directory = URL(fileURLWithPath: "/private/tmp", isDirectory: true)
            .appendingPathComponent("BOOTH-BUDDY-UI-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        syntheticDataPath = directory.path
    }

    override func tearDownWithError() throws {
        application?.terminate()
        application = nil
        if let syntheticDataPath {
            try? FileManager.default.removeItem(atPath: syntheticDataPath)
        }
    }

    @MainActor
    private func launchApp(additionalArguments: [String] = []) -> XCUIApplication {
        let app = XCUIApplication()
        application = app
        app.launchArguments = ["--ui-testing", "--synthetic-data", "-ApplePersistenceIgnoreState", "YES"] + additionalArguments
        app.launchEnvironment["AUTOCUE_DESKTOP_DATA"] = syntheticDataPath
        app.launch()
        if !app.windows.firstMatch.waitForExistence(timeout: 3) {
            app.typeKey("n", modifierFlags: .command)
        }
        XCTAssertTrue(app.buttons["sidebar-library"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.buttons["detailed-workspace-toggle"].waitForExistence(timeout: 10))
        let ready = NSPredicate(format: "enabled == true")
        let readyExpectation = XCTNSPredicateExpectation(predicate: ready, object: app.buttons["detailed-workspace-toggle"])
        let result = XCTWaiter.wait(for: [readyExpectation], timeout: 35)
        if result != .completed {
            let log = URL(fileURLWithPath: syntheticDataPath).appendingPathComponent("synthetic-backend-stderr.log")
            let detail = (try? String(contentsOf: log, encoding: .utf8)) ?? "No synthetic backend diagnostics."
            XCTFail("Backend did not become ready. \(detail.prefix(4000))\n\(app.debugDescription)")
        }
        return app
    }

    @MainActor
    func testLaunchShowsNativeWorkspace() throws {
        let app = launchApp()

        XCTAssertTrue(app.staticTexts["Library"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["sidebar-planning"].exists)
        XCTAssertTrue(app.buttons["sidebar-crates"].exists)
        XCTAssertTrue(app.buttons["sidebar-operations"].exists)
        XCTAssertTrue(app.buttons["sidebar-readiness"].exists)
        XCTAssertTrue(app.staticTexts["On this Mac"].exists)

        let screenshot = XCTAttachment(screenshot: app.windows.firstMatch.screenshot())
        screenshot.name = "native-workspace-launch"
        screenshot.lifetime = .keepAlways
        add(screenshot)
    }

    @MainActor
    func testSidebarNavigationChangesNativeWorkspace() throws {
        let app = launchApp()

        app.buttons["sidebar-planning"].click()
        XCTAssertTrue(app.staticTexts["Set planner"].waitForExistence(timeout: 3))
        XCTAssertTrue(app.staticTexts["Shape the night with intent."].exists)

        app.buttons["sidebar-crates"].click()
        XCTAssertTrue(app.staticTexts["Crates"].waitForExistence(timeout: 3))
        XCTAssertTrue(app.staticTexts["Keep every crate intentional."].exists)

        app.buttons["sidebar-readiness"].click()
        XCTAssertTrue(app.staticTexts["Readiness"].waitForExistence(timeout: 3))
        XCTAssertTrue(app.staticTexts["A calmer booth starts here."].exists)
    }

    @MainActor
    func testDetailedWorkspaceToggleIsNativeAndReversible() throws {
        let app = launchApp()
        let toggle = app.buttons["detailed-workspace-toggle"]

        XCTAssertTrue(toggle.waitForExistence(timeout: 3))
        toggle.click()
        XCTAssertTrue(app.staticTexts["Search the index without touching audio."].waitForExistence(timeout: 3))

        toggle.click()
        XCTAssertTrue(app.staticTexts["Your music, ready for the room."].waitForExistence(timeout: 3))
    }

    @MainActor
    func testWindowCanResizeWithoutLosingNavigation() throws {
        let app = launchApp()
        let window = app.windows.firstMatch

        XCTAssertTrue(window.waitForExistence(timeout: 3))
        XCTAssertGreaterThanOrEqual(window.frame.width, 860)
        // A previous window can reopen at minimum size; first establish room to shrink.
        if window.frame.width < 1050 {
            let grow = window.coordinate(withNormalizedOffset: CGVector(dx: 1, dy: 0.5))
                .withOffset(CGVector(dx: -1, dy: 0))
            grow.click(forDuration: 0.2, thenDragTo: grow.withOffset(CGVector(dx: 220, dy: 0)))
        }
        let originalWidth = window.frame.width
        XCTAssertGreaterThan(originalWidth, 950)
        let corner = window.coordinate(withNormalizedOffset: CGVector(dx: 1, dy: 0.5))
            .withOffset(CGVector(dx: -1, dy: 0))
        corner.click(forDuration: 0.2, thenDragTo: corner.withOffset(CGVector(dx: -160, dy: 0)))
        XCTAssertLessThan(window.frame.width, originalWidth - 20)
        XCTAssertTrue(app.buttons["sidebar-library"].exists)
        XCTAssertTrue(app.staticTexts["Your music, ready for the room."].exists)
        XCTAssertTrue(app.buttons["sidebar-readiness"].exists)
        let screenshot = XCTAttachment(screenshot: app.windows.firstMatch.screenshot())
        screenshot.name = "native-workspace-resized"
        screenshot.lifetime = .keepAlways
        add(screenshot)
    }

    @MainActor
    func testInvalidSyntheticDataOverrideShowsRecoveryState() throws {
        let app = XCUIApplication()
        application = app
        app.launchArguments = ["--ui-testing", "--synthetic-data", "-ApplePersistenceIgnoreState", "YES"]
        app.launchEnvironment["AUTOCUE_DESKTOP_DATA"] = "relative-ui-test-path"
        app.launch()
        if !app.windows.firstMatch.waitForExistence(timeout: 3) {
            app.typeKey("n", modifierFlags: .command)
        }

        XCTAssertTrue(app.staticTexts["BOOTH BUDDY could not start"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Retry"].exists)
        XCTAssertTrue(app.buttons["Quit"].exists)
    }

    @MainActor
    private func replaceText(_ element: XCUIElement, with text: String) {
        XCTAssertTrue(element.waitForExistence(timeout: 5))
        element.click()
        element.typeKey("a", modifierFlags: .command)
        element.typeText(text)
    }

    @MainActor
    private func showLibraryDetail(_ app: XCUIApplication) {
        app.buttons["detailed-workspace-toggle"].click()
        XCTAssertTrue(app.staticTexts["Search the index without touching audio."].waitForExistence(timeout: 5))
        app.scrollViews["workspace-scroll"].scroll(byDeltaX: 0, deltaY: -600)
    }

    @MainActor
    func testLibraryDraftSurvivesNavigationDetailAndRefresh() throws {
        let app = launchApp()
        let path = app.textFields["library-folder-path"]
        replaceText(path, with: "/tmp/BOOTH-BUDDY-unsubmitted-draft")
        app.buttons["sidebar-crates"].click()
        app.buttons["sidebar-library"].click()
        XCTAssertEqual(path.value as? String, "/tmp/BOOTH-BUDDY-unsubmitted-draft")
        app.typeKey("r", modifierFlags: .command)
        XCTAssertEqual(path.value as? String, "/tmp/BOOTH-BUDDY-unsubmitted-draft")
        app.typeKey("d", modifierFlags: [.command, .shift])
        XCTAssertTrue(app.staticTexts["Search the index without touching audio."].waitForExistence(timeout: 5))
        XCTAssertEqual(path.value as? String, "/tmp/BOOTH-BUDDY-unsubmitted-draft")
    }

    @MainActor
    func testPlannerDraftValidationAndSharedEditor() throws {
        let app = launchApp()
        app.buttons["sidebar-planning"].click()
        app.buttons["planner-advanced-toggle"].click()
        app.scrollViews["workspace-scroll"].scroll(byDeltaX: 0, deltaY: -500)
        let policy = app.textViews["planner-policy-json"]
        replaceText(policy, with: "{")
        XCTAssertFalse(app.buttons["planner-generate"].isEnabled)
        XCTAssertTrue(app.staticTexts["Event data and planner policy must each be a valid JSON object."].exists)
        app.buttons["detailed-workspace-toggle"].click()
        app.buttons["planner-advanced-toggle"].click()
        app.scrollViews["workspace-scroll"].scroll(byDeltaX: 0, deltaY: -500)
        XCTAssertEqual(policy.value as? String, "{")
        replaceText(policy, with: "{}")
        XCTAssertTrue(app.buttons["planner-generate"].isEnabled)
        app.buttons["sidebar-crates"].click()
        app.buttons["sidebar-planning"].click()
        app.buttons["planner-advanced-toggle"].click()
        app.scrollViews["workspace-scroll"].scroll(byDeltaX: 0, deltaY: -500)
        XCTAssertEqual(policy.value as? String, "{}")
        app.typeKey("r", modifierFlags: .command)
        app.buttons["planner-advanced-toggle"].click()
        app.scrollViews["workspace-scroll"].scroll(byDeltaX: 0, deltaY: -500)
        XCTAssertEqual(policy.value as? String, "{}")
    }

    @MainActor
    func testCatalogSearchClearFilterAndRefresh() throws {
        let app = launchApp()
        showLibraryDetail(app)
        let search = app.textFields["catalog-search"]
        replaceText(search, with: "synthetic-no-match")
        XCTAssertTrue(app.staticTexts["No matching tracks. Try another search or turn off the clean filter."].waitForExistence(timeout: 10))
        app.typeKey("r", modifierFlags: .command)
        XCTAssertEqual(search.value as? String, "synthetic-no-match")
        app.scrollViews["workspace-scroll"].scroll(byDeltaX: 0, deltaY: -600)
        app.buttons["catalog-clear-search"].click()
        XCTAssertTrue(app.staticTexts["No searchable tracks yet. Scan files with title and artist tags to begin."].waitForExistence(timeout: 10))
        app.checkBoxes["catalog-clean-only"].click()
        XCTAssertTrue(app.staticTexts["No matching tracks. Try another search or turn off the clean filter."].waitForExistence(timeout: 10))
        let screenshot = XCTAttachment(screenshot: app.windows.firstMatch.screenshot())
        screenshot.name = "native-catalog-search"
        screenshot.lifetime = .keepAlways
        add(screenshot)
    }

    @MainActor
    func testReadinessDisplaysBackendReleaseEvidence() throws {
        let app = launchApp()
        app.buttons["sidebar-readiness"].click()
        app.buttons["detailed-workspace-toggle"].click()
        XCTAssertTrue(app.staticTexts["release-gate-count"].waitForExistence(timeout: 10))
        let count = app.staticTexts["release-gate-count"]
        XCTAssertEqual(count.label.isEmpty ? count.value as? String : count.label, "8 release gates pending")
        XCTAssertTrue(app.staticTexts["Local alpha · release gates pending"].exists)
        let screenshot = XCTAttachment(screenshot: app.windows.firstMatch.screenshot())
        screenshot.name = "native-release-evidence"
        screenshot.lifetime = .keepAlways
        add(screenshot)
    }

    @MainActor
    func testGeneratedAudioScanCompletionAndSearch() throws {
        let music = URL(fileURLWithPath: syntheticDataPath).appendingPathComponent("generated-music", isDirectory: true)
        try FileManager.default.createDirectory(at: music, withIntermediateDirectories: true)
        // One tenth of a second of generated PCM silence. Never inspect real audio.
        var wav = Data()
        func ascii(_ value: String) { wav.append(contentsOf: value.utf8) }
        func little(_ value: UInt32, bytes: Int) {
            for shift in 0..<bytes { wav.append(UInt8((value >> (shift * 8)) & 255)) }
        }
        ascii("RIFF"); little(1636, bytes: 4); ascii("WAVEfmt "); little(16, bytes: 4)
        little(1, bytes: 2); little(1, bytes: 2); little(8000, bytes: 4); little(16000, bytes: 4)
        little(2, bytes: 2); little(16, bytes: 2); ascii("data"); little(1600, bytes: 4)
        wav.append(Data(repeating: 0, count: 1600))
        let title = Array("Booth Buddy Synthetic".utf8)
        // ID3v2.3 TIT2 frame with an ISO-8859-1 title inside a RIFF ID3 chunk.
        let frame = Array("TIT2".utf8) + [0, 0, 0, UInt8(title.count + 1), 0, 0, 0] + title
        let artist = Array("Invented Test Artist".utf8)
        let artistFrame = Array("TPE1".utf8) + [0, 0, 0, UInt8(artist.count + 1), 0, 0, 0] + artist
        let frames = frame + artistFrame
        let tag = Array("ID3".utf8) + [3, 0, 0, 0, 0, 0, UInt8(frames.count)] + frames
        ascii("id3 "); little(UInt32(tag.count), bytes: 4); wav.append(contentsOf: tag)
        if tag.count % 2 != 0 { wav.append(0) }
        let riffSize = UInt32(wav.count - 8)
        for shift in 0..<4 { wav[4 + shift] = UInt8((riffSize >> (shift * 8)) & 255) }
        let fixture = music.appendingPathComponent("Booth Buddy Synthetic.wav")
        try wav.write(to: fixture)
        let app = launchApp()
        replaceText(app.textFields["library-folder-path"], with: music.path)
        app.buttons["library-start-scan"].click()
        XCTAssertTrue(app.staticTexts["scan-status-completed"].waitForExistence(timeout: 30))
        XCTAssertEqual(try Data(contentsOf: fixture), wav, "Scanning must preserve source bytes")
        showLibraryDetail(app)
        replaceText(app.textFields["catalog-search"], with: "Booth Buddy Synthetic")
        let oneMatch = NSPredicate(format: "label == %@ OR value == %@", "1 matching track", "1 matching track")
        expectation(for: oneMatch, evaluatedWith: app.staticTexts["catalog-result-count"])
        waitForExpectations(timeout: 10)
        let screenshot = XCTAttachment(screenshot: app.windows.firstMatch.screenshot())
        screenshot.name = "native-generated-scan-search"
        screenshot.lifetime = .keepAlways
        add(screenshot)
    }

    @MainActor
    func testQuitAndRelaunchReleasesLocalWorkspace() throws {
        let app = launchApp()
        app.typeKey("q", modifierFlags: .command)
        XCTAssertTrue(app.wait(for: .notRunning, timeout: 10))
        _ = launchApp()
        XCTAssertTrue(app.buttons["sidebar-library"].exists)
    }

    @MainActor
    private func reveal(_ element: XCUIElement, in app: XCUIApplication) {
        XCTAssertTrue(element.waitForExistence(timeout: 15))
        let scroll = app.scrollViews["workspace-scroll"]
        for _ in 0..<20 {
            let viewport = scroll.frame.intersection(app.windows.firstMatch.frame).insetBy(dx: 2, dy: 6)
            let center = CGPoint(x: element.frame.midX, y: element.frame.midY)
            if element.isHittable && viewport.contains(center) { return }
            // SwiftUI can report a clipped control as hittable. Reveal its center,
            // including controls above the current scroll position.
            scroll.scroll(byDeltaX: 0, deltaY: center.y < viewport.minY ? 350 : -350)
        }
        XCTFail("Control could not be revealed: \(element.debugDescription)")
    }

    private func generatedFixture(seconds: Int = 60, title: String = "Native Planned Track") throws -> (URL, Data) {
        let music = URL(fileURLWithPath: syntheticDataPath).appendingPathComponent("planned-music", isDirectory: true)
        try FileManager.default.createDirectory(at: music, withIntermediateDirectories: true)
        var wav = Data()
        func ascii(_ value: String) { wav.append(contentsOf: value.utf8) }
        func little(_ value: UInt32, bytes: Int) {
            for shift in 0..<bytes { wav.append(UInt8((value >> (shift * 8)) & 255)) }
        }
        let byteCount = seconds * 16000
        ascii("RIFF"); little(UInt32(byteCount + 36), bytes: 4); ascii("WAVEfmt "); little(16, bytes: 4)
        little(1, bytes: 2); little(1, bytes: 2); little(8000, bytes: 4); little(16000, bytes: 4)
        little(2, bytes: 2); little(16, bytes: 2); ascii("data"); little(UInt32(byteCount), bytes: 4)
        wav.append(Data(repeating: 0, count: byteCount))
        func tagFrame(_ id: String, _ value: String) -> [UInt8] {
            let text = Array(value.utf8)
            return Array(id.utf8) + [0, 0, 0, UInt8(text.count + 1), 0, 0, 0] + text
        }
        let frames = tagFrame("TIT2", title) + tagFrame("TPE1", "Invented Native Artist")
        let tag = Array("ID3".utf8) + [3, 0, 0, 0, 0, 0, UInt8(frames.count)] + frames
        ascii("id3 "); little(UInt32(tag.count), bytes: 4); wav.append(contentsOf: tag)
        if tag.count % 2 != 0 { wav.append(0) }
        let riffSize = UInt32(wav.count - 8)
        for shift in 0..<4 { wav[4 + shift] = UInt8((riffSize >> (shift * 8)) & 255) }
        let file = music.appendingPathComponent(title + ".wav")
        try wav.write(to: file)
        return (file, wav)
    }

    @MainActor
    private func scanAndGenerate(_ app: XCUIApplication, fixture: URL, targetMinutes: String = "1") throws {
        replaceText(app.textFields["library-folder-path"], with: fixture.deletingLastPathComponent().path)
        app.buttons["library-start-scan"].click()
        XCTAssertTrue(app.staticTexts["scan-status-completed"].waitForExistence(timeout: 30))
        app.buttons["sidebar-planning"].click()
        replaceText(app.textFields["planner-target-minutes"], with: targetMinutes)
        replaceText(app.textFields["planner-tolerance-minutes"], with: "0")
        // Commit numeric text fields before generation.
        app.textFields["planner-candidate-search"].click()
        reveal(app.buttons["planner-generate"], in: app)
        app.buttons["planner-generate"].click()
        XCTAssertTrue(app.staticTexts["plan-alternative-0"].waitForExistence(timeout: 30), app.debugDescription)
        let status = app.staticTexts["planner-status"]
        XCTAssertTrue((status.value as? String ?? status.label).contains("Feasible"))
    }

    @MainActor
    private func approveFirstAlternative(_ app: XCUIApplication) throws {
        let preview = app.buttons["plan-preview-0"]
        reveal(preview, in: app); preview.click()
        let approve = app.buttons["plan-approve"]
        reveal(approve, in: app); approve.click()
    }

    @MainActor
    private func saveDialog(_ app: XCUIApplication) {
        let save = app.sheets.buttons["Save"]
        XCTAssertTrue(save.waitForExistence(timeout: 10), app.debugDescription)
        save.click()
    }

    private func outputOf(_ executable: String, _ arguments: [String]) throws -> Data {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: executable)
        process.arguments = arguments
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = FileHandle.nullDevice
        try process.run()
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        XCTAssertEqual(process.terminationStatus, 0)
        return data
    }

    @MainActor
    func testNativePlanApprovalRestartAndVerifiedExport() throws {
        let (fixture, original) = try generatedFixture()
        let app = launchApp()
        try scanAndGenerate(app, fixture: fixture)
        try approveFirstAlternative(app)
        XCTAssertTrue(app.staticTexts["native-saved-crate-name"].waitForExistence(timeout: 15), app.debugDescription)
        app.typeKey("q", modifierFlags: .command)
        XCTAssertTrue(app.wait(for: .notRunning, timeout: 10))
        let reopened = launchApp()
        reopened.buttons["sidebar-crates"].click()
        reopened.buttons["detailed-workspace-toggle"].click()
        XCTAssertTrue(reopened.staticTexts["native-saved-crate-name"].waitForExistence(timeout: 10))
        let preview = reopened.buttons["native-export-preview"].firstMatch
        reveal(preview, in: reopened); preview.click()
        let counts = reopened.staticTexts["native-export-counts"].firstMatch
        XCTAssertTrue(counts.waitForExistence(timeout: 15))
        XCTAssertEqual(counts.value as? String ?? counts.label, "1 requested · 1 verified · 0 blocked")
        let save = reopened.buttons["native-export-save"].firstMatch
        reveal(save, in: reopened); save.click()
        saveDialog(reopened)
        let output = URL(fileURLWithPath: syntheticDataPath).appendingPathComponent("BOOTH BUDDY playlist.zip")
        let saved = NSPredicate { _, _ in FileManager.default.fileExists(atPath: output.path) }
        expectation(for: saved, evaluatedWith: nil)
        waitForExpectations(timeout: 15)
        let playlist = try outputOf("/usr/bin/unzip", ["-p", output.path, "playlist.m3u8"])
        XCTAssertEqual(String(data: playlist, encoding: .utf8), "#EXTM3U\n" + fixture.path + "\n")
        let manifest = try JSONSerialization.jsonObject(with: outputOf("/usr/bin/unzip", ["-p", output.path, "manifest.json"])) as! [String: Any]
        XCTAssertEqual(manifest["included"] as? Int, 1)
        XCTAssertEqual(manifest["blocked"] as? Int, 0)
        XCTAssertEqual(try Data(contentsOf: fixture), original)
        let savedBytes = try Data(contentsOf: output)
        try FileManager.default.removeItem(at: output)
        let offline = fixture.appendingPathExtension("offline")
        try FileManager.default.moveItem(at: fixture, to: offline)
        defer { try? FileManager.default.moveItem(at: offline, to: fixture) }
        let history = reopened.buttons["native-history-save"].firstMatch
        reveal(history, in: reopened); history.click(); saveDialog(reopened)
        expectation(for: saved, evaluatedWith: nil)
        waitForExpectations(timeout: 15)
        XCTAssertEqual(try Data(contentsOf: output), savedBytes, "Historical recovery must preserve the exact old artifact without probing offline source audio")
        XCTAssertEqual(try Data(contentsOf: offline), original)
        let screenshot = XCTAttachment(screenshot: reopened.windows.firstMatch.screenshot())
        screenshot.name = "native-approved-plan-export"; screenshot.lifetime = .keepAlways; add(screenshot)
    }

    @MainActor
    func testLostApprovalAcknowledgementReconcilesAfterRestartWithoutDuplicate() throws {
        let (fixture, original) = try generatedFixture()
        let app = launchApp(additionalArguments: ["--test-drop-approval-ack"])
        try scanAndGenerate(app, fixture: fixture)
        try approveFirstAlternative(app)
        XCTAssertTrue(app.staticTexts["native-pending-approval"].waitForExistence(timeout: 15))
        let journalURL = URL(fileURLWithPath: syntheticDataPath).appendingPathComponent("native-workflow-v1.json")
        let before = try JSONSerialization.jsonObject(with: Data(contentsOf: journalURL)) as! [String: Any]
        let request = (before["pending"] as! [String: Any])["requestId"] as! String
        app.typeKey("q", modifierFlags: .command)
        XCTAssertTrue(app.wait(for: .notRunning, timeout: 10))
        let reopened = launchApp()
        reopened.buttons["sidebar-planning"].click()
        let retry = reopened.buttons["native-retry-approval"]
        reveal(retry, in: reopened); retry.click()
        XCTAssertTrue(reopened.staticTexts["native-saved-crate-name"].waitForExistence(timeout: 15), reopened.debugDescription)
        XCTAssertFalse(reopened.staticTexts["native-pending-approval"].exists)
        let after = try JSONSerialization.jsonObject(with: Data(contentsOf: journalURL)) as! [String: Any]
        XCTAssertNil(after["pending"])
        let saved = after["saved"] as! [[String: Any]]
        XCTAssertEqual(saved.count, 1)
        XCTAssertEqual(saved[0]["requestId"] as? String, request)
        let count = try outputOf("/usr/bin/sqlite3", [URL(fileURLWithPath: syntheticDataPath).appendingPathComponent("library.sqlite3").path,
                                                     "SELECT COUNT(*) FROM personal_crates;"])
        XCTAssertEqual(String(data: count, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines), "1")
        XCTAssertEqual(try Data(contentsOf: fixture), original)
    }

    @MainActor
    func testChangedSourceBlocksNativeExportAfterPreview() throws {
        let (fixture, _) = try generatedFixture()
        let app = launchApp()
        try scanAndGenerate(app, fixture: fixture)
        try approveFirstAlternative(app)
        XCTAssertTrue(app.staticTexts["native-saved-crate-name"].waitForExistence(timeout: 15))
        let preview = app.buttons["native-export-preview"].firstMatch
        reveal(preview, in: app); preview.click()
        XCTAssertTrue(app.staticTexts["native-export-counts"].firstMatch.waitForExistence(timeout: 15))
        let handle = try FileHandle(forWritingTo: fixture)
        try handle.seekToEnd(); try handle.write(contentsOf: Data([1])); try handle.close()
        let save = app.buttons["native-export-save"].firstMatch
        reveal(save, in: app); save.click(); saveDialog(app)
        XCTAssertTrue(app.staticTexts["native-export-error"].waitForExistence(timeout: 15), app.debugDescription)
        XCTAssertFalse(FileManager.default.fileExists(atPath: URL(fileURLWithPath: syntheticDataPath)
            .appendingPathComponent("BOOTH BUDDY playlist.zip").path))
    }

    @MainActor
    func testNativePlannerReportsInfeasibleWithoutSaving() throws {
        let (fixture, _) = try generatedFixture()
        let app = launchApp()
        try scanAndGenerate(app, fixture: fixture)
        // A single 60-second file cannot fill an exact 120-second, no-repeat set.
        app.scrollViews["workspace-scroll"].scroll(byDeltaX: 0, deltaY: 1500)
        replaceText(app.textFields["planner-target-minutes"], with: "2")
        app.textFields["planner-candidate-search"].click()
        reveal(app.buttons["planner-generate"], in: app); app.buttons["planner-generate"].click()
        let infeasible = NSPredicate(format: "value CONTAINS %@ OR label CONTAINS %@", "Infeasible", "Infeasible")
        expectation(for: infeasible, evaluatedWith: app.staticTexts["planner-status"])
        waitForExpectations(timeout: 15)
        XCTAssertFalse(app.buttons["plan-preview-0"].exists)
        XCTAssertFalse(app.buttons["plan-approve"].exists)
        XCTAssertFalse(app.staticTexts["native-saved-crate-name"].exists)
    }

    @MainActor
    func testNativePlannerRefusesImplicitCandidateTruncation() throws {
        let (fixture, _) = try generatedFixture(seconds: 1, title: "Scope fixture")
        for index in 0..<100 {
            try FileManager.default.copyItem(at: fixture, to: fixture.deletingLastPathComponent().appendingPathComponent("scope-\(index).wav"))
        }
        let app = launchApp()
        replaceText(app.textFields["library-folder-path"], with: fixture.deletingLastPathComponent().path)
        app.buttons["library-start-scan"].click()
        XCTAssertTrue(app.staticTexts["scan-status-completed"].waitForExistence(timeout: 30))
        app.buttons["sidebar-planning"].click()
        app.popUpButtons["planner-candidate-limit"].click()
        app.menuItems["Up to 100 matching files"].click()
        reveal(app.buttons["planner-generate"], in: app); app.buttons["planner-generate"].click()
        let scopeError = NSPredicate(format: "value CONTAINS %@ OR label CONTAINS %@", "101 matching files exceed", "101 matching files exceed")
        expectation(for: scopeError, evaluatedWith: app.staticTexts["planner-message"])
        waitForExpectations(timeout: 15)
        XCTAssertFalse(app.staticTexts["plan-alternative-0"].exists)
        XCTAssertFalse(app.buttons["plan-approve"].exists)
    }

    @MainActor
    func testCorruptNativeRecoveryJournalIsPreservedAndBlocksApproval() throws {
        let url = URL(fileURLWithPath: syntheticDataPath).appendingPathComponent("native-workflow-v1.json")
        let original = Data("not a valid recovery journal".utf8)
        try original.write(to: url)
        let app = launchApp()
        app.buttons["sidebar-planning"].click()
        XCTAssertTrue(app.staticTexts["native-recovery-error"].waitForExistence(timeout: 10))
        XCTAssertEqual(try Data(contentsOf: url), original)
        XCTAssertFalse(app.buttons["plan-approve"].exists)
    }

    @MainActor
    private func openCrateManager(_ app: XCUIApplication) {
        app.buttons["sidebar-crates"].click()
        app.buttons["detailed-workspace-toggle"].click()
        XCTAssertTrue(app.staticTexts["native-current-crate-name"].waitForExistence(timeout: 15))
    }

    @MainActor
    func testNativeCrateRenameApprovalRestartAndUndo() throws {
        let (fixture, original) = try generatedFixture()
        let app = launchApp()
        try scanAndGenerate(app, fixture: fixture); try approveFirstAlternative(app)
        XCTAssertTrue(app.staticTexts["native-saved-crate-name"].waitForExistence(timeout: 15))
        openCrateManager(app)
        let name = app.textFields["native-edit-name"]
        reveal(name, in: app); replaceText(name, with: "Reviewed commercial test crate")
        let preview = app.buttons["native-edit-preview"]
        reveal(preview, in: app); preview.click()
        let approve = app.buttons["native-edit-approve"]
        reveal(approve, in: app); approve.click()
        let renamed = NSPredicate(format: "value == %@ OR label == %@", "Reviewed commercial test crate", "Reviewed commercial test crate")
        expectation(for: renamed, evaluatedWith: app.staticTexts["native-current-crate-name"])
        waitForExpectations(timeout: 15)
        app.typeKey("q", modifierFlags: .command); XCTAssertTrue(app.wait(for: .notRunning, timeout: 10))
        let reopened = launchApp(); openCrateManager(reopened)
        XCTAssertEqual(reopened.textFields["native-edit-name"].value as? String, "Reviewed commercial test crate")
        let reviewUndo = reopened.buttons["native-edit-review-undo"].firstMatch
        reveal(reviewUndo, in: reopened); reviewUndo.click()
        let confirm = reopened.buttons["native-edit-confirm-undo"]
        reveal(confirm, in: reopened); confirm.click()
        let restored = NSPredicate(format: "value == %@ OR label == %@", "New planned set", "New planned set")
        expectation(for: restored, evaluatedWith: reopened.staticTexts["native-current-crate-name"])
        waitForExpectations(timeout: 15)
        XCTAssertEqual(try Data(contentsOf: fixture), original)
        let screenshot = XCTAttachment(screenshot: reopened.windows.firstMatch.screenshot())
        screenshot.name = "native-crate-edit-undo"; screenshot.lifetime = .keepAlways; add(screenshot)
    }

    @MainActor
    func testNativeCrateMembershipReorderRemovalAndUndo() throws {
        let (fixture, original) = try generatedFixture(title: "First Synthetic Track")
        _ = try generatedFixture(title: "Second Synthetic Track")
        _ = try generatedFixture(title: "Third Synthetic Track")
        let app = launchApp()
        try scanAndGenerate(app, fixture: fixture, targetMinutes: "3"); try approveFirstAlternative(app)
        XCTAssertTrue(app.staticTexts["native-saved-crate-name"].waitForExistence(timeout: 15))
        openCrateManager(app)
        let up = app.buttons["crate-up-1"]; reveal(up, in: app); up.click()
        app.buttons["crate-remove-2"].click()
        let preview = app.buttons["native-edit-preview"]; reveal(preview, in: app); preview.click()
        let approve = app.buttons["native-edit-approve"]; reveal(approve, in: app); approve.click()
        let revisionTwo = NSPredicate(format: "value CONTAINS %@ OR label CONTAINS %@", "revision 2", "revision 2")
        expectation(for: revisionTwo, evaluatedWith: app.staticTexts["native-current-crate-revision"]); waitForExpectations(timeout: 15)
        let database = URL(fileURLWithPath: syntheticDataPath).appendingPathComponent("library.sqlite3").path
        let order = try outputOf("/usr/bin/sqlite3", [database, "SELECT group_concat(id, ',') FROM (SELECT id FROM personal_crate_entries ORDER BY position);"])
        XCTAssertEqual(String(data: order, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines), "1,0")
        let undo = app.buttons["native-edit-review-undo"].firstMatch; reveal(undo, in: app); undo.click()
        let confirm = app.buttons["native-edit-confirm-undo"]; reveal(confirm, in: app); confirm.click()
        let revisionThree = NSPredicate(format: "value CONTAINS %@ OR label CONTAINS %@", "revision 3", "revision 3")
        expectation(for: revisionThree, evaluatedWith: app.staticTexts["native-current-crate-revision"]); waitForExpectations(timeout: 15)
        let restored = try outputOf("/usr/bin/sqlite3", [database, "SELECT group_concat(id, ',') FROM (SELECT id FROM personal_crate_entries ORDER BY position);"])
        XCTAssertEqual(String(data: restored, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines), "0,1,2")
        XCTAssertEqual(try Data(contentsOf: fixture), original)
    }

    @MainActor
    func testNativeEditLostResponseReconcilesAfterRestart() throws {
        let (fixture, original) = try generatedFixture()
        let app = launchApp(additionalArguments: ["--test-drop-edit-ack"])
        try scanAndGenerate(app, fixture: fixture); try approveFirstAlternative(app)
        XCTAssertTrue(app.staticTexts["native-saved-crate-name"].waitForExistence(timeout: 15))
        openCrateManager(app)
        let name = app.textFields["native-edit-name"]; reveal(name, in: app); replaceText(name, with: "Recover this exact rename")
        let preview = app.buttons["native-edit-preview"]; reveal(preview, in: app); preview.click()
        let approve = app.buttons["native-edit-approve"]; reveal(approve, in: app); approve.click()
        XCTAssertTrue(app.staticTexts["native-pending-edit"].waitForExistence(timeout: 15))
        app.typeKey("q", modifierFlags: .command); XCTAssertTrue(app.wait(for: .notRunning, timeout: 10))
        let reopened = launchApp(); openCrateManager(reopened)
        let retry = reopened.buttons["native-retry-edit"]; reveal(retry, in: reopened); retry.click()
        let recovered = NSPredicate(format: "exists == false")
        expectation(for: recovered, evaluatedWith: reopened.staticTexts["native-pending-edit"]); waitForExpectations(timeout: 15)
        let database = URL(fileURLWithPath: syntheticDataPath).appendingPathComponent("library.sqlite3").path
        let state = try outputOf("/usr/bin/sqlite3", [database, "SELECT name,revision FROM personal_crates;"])
        XCTAssertEqual(String(data: state, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines), "Recover this exact rename|2")
        XCTAssertEqual(try Data(contentsOf: fixture), original)
    }

    @MainActor
    func testNativeBackupRestoreAndOpenNewWorkspace() throws {
        let (fixture, original) = try generatedFixture()
        let app = launchApp()
        try scanAndGenerate(app, fixture: fixture); try approveFirstAlternative(app)
        XCTAssertTrue(app.staticTexts["native-saved-crate-name"].waitForExistence(timeout: 15))
        app.buttons["sidebar-operations"].click(); app.buttons["detailed-workspace-toggle"].click()
        let backupButton = app.buttons["native-backup-save"]; reveal(backupButton, in: app); backupButton.click(); saveDialog(app)
        let root = URL(fileURLWithPath: syntheticDataPath)
        let archive = root.appendingPathComponent("BOOTH BUDDY database backup.zip")
        let backupSaved = NSPredicate { _, _ in FileManager.default.fileExists(atPath: archive.path) }
        expectation(for: backupSaved, evaluatedWith: nil); waitForExpectations(timeout: 40)
        let restore = app.buttons["native-backup-restore"]; restore.click()
        XCTAssertTrue(app.sheets.buttons["Open"].waitForExistence(timeout: 10))
        let archiveRow = app.sheets.textFields.matching(NSPredicate(format: "value == %@", archive.lastPathComponent)).firstMatch
        XCTAssertTrue(archiveRow.waitForExistence(timeout: 10)); archiveRow.doubleClick()
        let restoreDestination = app.sheets.buttons["Restore"]
        XCTAssertTrue(restoreDestination.waitForExistence(timeout: 10)); restoreDestination.click()
        let openRestored = app.buttons["native-backup-open-restored"]
        XCTAssertTrue(openRestored.waitForExistence(timeout: 40), app.debugDescription); openRestored.click()
        XCTAssertTrue(app.staticTexts["Your music, ready for the room."].waitForExistence(timeout: 15))
        openCrateManager(app)
        XCTAssertEqual(app.textFields["native-edit-name"].value as? String, "New planned set")
        let restored = root.appendingPathComponent("BOOTH BUDDY Restored/library.sqlite3")
        XCTAssertTrue(FileManager.default.fileExists(atPath: restored.path))
        let count = try outputOf("/usr/bin/sqlite3", [restored.path, "SELECT count(*) FROM personal_crates;"])
        XCTAssertEqual(String(data: count, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines), "1")
        XCTAssertTrue(FileManager.default.fileExists(atPath: root.appendingPathComponent("library.sqlite3").path))
        XCTAssertEqual(try Data(contentsOf: fixture), original)
        let screenshot = XCTAttachment(screenshot: app.windows.firstMatch.screenshot())
        screenshot.name = "native-restored-workspace"; screenshot.lifetime = .keepAlways; add(screenshot)
    }

    @MainActor
    func testNativeEventRequestsAndPhasesPreserveDrafts() throws {
        let app = launchApp()
        app.buttons["sidebar-planning"].click()
        let details = app.buttons["native-planning-details"]; reveal(details, in: app); details.click()
        let title = app.textFields["event-request-title"]; reveal(title, in: app); replaceText(title, with: "Synthetic request")
        replaceText(app.textFields["event-request-artist"], with: "Synthetic artist")
        let addRequest = app.buttons["event-request-add"]; reveal(addRequest, in: app); addRequest.click()
        XCTAssertTrue(app.buttons["event-request-remove-0"].exists)
        let label = app.textFields["phase-label"]; reveal(label, in: app); replaceText(label, with: "Synthetic phase")
        let addPhase = app.buttons["phase-add"]; reveal(addPhase, in: app); addPhase.click()
        XCTAssertTrue(app.buttons["phase-remove-0"].exists)
        let budget = app.textFields["phase-budget-0"]; reveal(budget, in: app); replaceText(budget, with: "8")
        reveal(label, in: app); replaceText(label, with: "Second synthetic phase")
        reveal(addPhase, in: app); addPhase.click()
        let move = app.buttons["Move phase 2 up"]; reveal(move, in: app); move.click()
        app.buttons["sidebar-crates"].click(); app.buttons["sidebar-planning"].click()
        app.buttons["planner-advanced-toggle"].click()
        let event = app.textViews["planner-event-json"]; reveal(event, in: app)
        let eventObject = try XCTUnwrap(JSONSerialization.jsonObject(with: Data((event.value as? String ?? "").utf8)) as? [String: Any])
        let lists = try XCTUnwrap(eventObject["music_lists"] as? [String: Any])
        let requests = try XCTUnwrap(lists["must_play"] as? [[String: Any]])
        XCTAssertEqual(requests.first?["title"] as? String, "Synthetic request")
        let policy = app.textViews["planner-policy-json"]; reveal(policy, in: app)
        let policyObject = try XCTUnwrap(JSONSerialization.jsonObject(with: Data((policy.value as? String ?? "").utf8)) as? [String: Any])
        let phases = try XCTUnwrap(policyObject["phases"] as? [[String: Any]])
        XCTAssertEqual(phases.first?["label"] as? String, "Second synthetic phase")
        XCTAssertEqual(phases.last?["label"] as? String, "Synthetic phase")
        XCTAssertEqual((phases.last?["target_minutes"] as? NSNumber)?.doubleValue, 8)
    }

    @MainActor
    func testNativeSupportReportContainsNoWorkspacePaths() throws {
        let app = launchApp()
        app.buttons["sidebar-readiness"].click(); app.buttons["detailed-workspace-toggle"].click()
        let support = app.buttons["native-support-save"]; reveal(support, in: app); support.click(); saveDialog(app)
        let report = URL(fileURLWithPath: syntheticDataPath).appendingPathComponent("BOOTH BUDDY support report.json")
        expectation(for: NSPredicate { _, _ in FileManager.default.fileExists(atPath: report.path) }, evaluatedWith: nil)
        waitForExpectations(timeout: 15)
        let data = try Data(contentsOf: report)
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertEqual(object["product"] as? String, "BOOTH BUDDY")
        XCTAssertFalse(String(decoding: data, as: UTF8.self).contains(syntheticDataPath))
        XCTAssertFalse(String(decoding: data, as: UTF8.self).contains(NSHomeDirectory()))
        XCTAssertNil(object["token"]); XCTAssertNil(object["environment"])
        let attributes = try FileManager.default.attributesOfItem(atPath: report.path)
        XCTAssertEqual((attributes[.posixPermissions] as? NSNumber)?.intValue, 0o600)
    }

    @MainActor
    func testNativeLightAppearanceAndKeyboardNavigation() throws {
        let app = launchApp(additionalArguments: ["--test-light-appearance"])
        app.buttons["sidebar-library"].click()
        app.typeKey("d", modifierFlags: [.command, .shift])
        XCTAssertTrue(app.staticTexts["Search the index without touching audio."].waitForExistence(timeout: 5))
        app.typeKey("f", modifierFlags: .command)
        app.typeText("Synthetic keyboard search")
        XCTAssertEqual(app.textFields["catalog-search"].value as? String, "Synthetic keyboard search")
        let screenshot = XCTAttachment(screenshot: app.windows.firstMatch.screenshot())
        screenshot.name = "native-light-appearance"; screenshot.lifetime = .keepAlways; add(screenshot)
    }

    @MainActor
    func testNativeSeratoCrateExportPreservesExistingFiles() throws {
        let (fixture, original) = try generatedFixture()
        let app = launchApp()
        try scanAndGenerate(app, fixture: fixture); try approveFirstAlternative(app)
        XCTAssertTrue(app.staticTexts["native-saved-crate-name"].waitForExistence(timeout: 15))
        openCrateManager(app)
        let fileStatus = app.staticTexts["gig-file-status"].firstMatch
        reveal(fileStatus, in: app)
        XCTAssertTrue((fileStatus.value as? String ?? fileStatus.label).contains("not checked"))
        let eventReview = app.checkBoxes["gig-event-review"].firstMatch
        reveal(eventReview, in: app); eventReview.click()
        let preview = app.buttons["native-export-preview"].firstMatch; reveal(preview, in: app); preview.click()
        let export = app.buttons["native-export-serato"].firstMatch; reveal(export, in: app); export.click()
        let exportSheet = app.sheets.buttons["Export"]
        XCTAssertTrue(exportSheet.waitForExistence(timeout: 10)); exportSheet.click()
        let crate = URL(fileURLWithPath: syntheticDataPath).appendingPathComponent("New planned set.crate")
        expectation(for: NSPredicate { _, _ in FileManager.default.fileExists(atPath: crate.path) }, evaluatedWith: nil)
        waitForExpectations(timeout: 20)
        let bytes = try Data(contentsOf: crate)
        XCTAssertEqual(String(data: bytes.prefix(4), encoding: .utf8), "vrsn")
        XCTAssertNotNil(bytes.range(of: try XCTUnwrap(fixture.path.data(using: .utf16BigEndian))))
        let attributes = try FileManager.default.attributesOfItem(atPath: crate.path)
        XCTAssertEqual((attributes[.posixPermissions] as? NSNumber)?.intValue, 0o600)
        reveal(eventReview, in: app)
        let reviewValue = eventReview.value
        XCTAssertEqual((reviewValue as? NSNumber)?.intValue ?? Int(reviewValue as? String ?? ""), 0, "Verifying again resets manual acknowledgements")
        reveal(fileStatus, in: app)
        XCTAssertTrue((fileStatus.value as? String ?? fileStatus.label).contains("last preview"))
        let message = app.staticTexts["native-export-message"].firstMatch
        XCTAssertTrue(message.waitForExistence(timeout: 10))
        XCTAssertTrue((message.value as? String ?? message.label).contains("_Serato_/Subcrates"))
        reveal(export, in: app); export.click()
        XCTAssertTrue(exportSheet.waitForExistence(timeout: 10)); exportSheet.click()
        let replace = app.buttons["Replace"]
        if replace.waitForExistence(timeout: 3) { replace.click() }
        let error = app.staticTexts["native-export-error"].firstMatch
        XCTAssertTrue(error.waitForExistence(timeout: 10))
        XCTAssertTrue((error.value as? String ?? error.label).contains("Existing Serato crates will not be replaced"))
        XCTAssertEqual(try Data(contentsOf: crate), bytes)
        XCTAssertEqual(try Data(contentsOf: fixture), original)
        let shot = XCTAttachment(screenshot: app.windows.firstMatch.screenshot())
        shot.name = "native-serato-crate-export"; shot.lifetime = .keepAlways; add(shot)
    }

    @MainActor
    func testRequestIntakeReviewsBeforeAppendAndReachesPlanner() throws {
        let (fixture, original) = try generatedFixture(title: "Imported Request Fixture")
        let app = launchApp()
        try scanAndGenerate(app, fixture: fixture)
        let intake = app.buttons["request-intake-toggle"]; reveal(intake, in: app); intake.click()
        let source = app.textViews["request-intake-source"]; reveal(source, in: app)
        replaceText(source, with: "Discard this draft")
        let preview = app.buttons["request-intake-preview"]; reveal(preview, in: app); preview.click()
        let discard = app.buttons["request-intake-discard"]; reveal(discard, in: app); discard.click()
        XCTAssertFalse(app.buttons["request-intake-apply"].exists)
        reveal(source, in: app); replaceText(source, with: "Imported Request Fixture")
        reveal(preview, in: app); preview.click()
        let artist = app.textFields["intake-artist-1"]; reveal(artist, in: app); replaceText(artist, with: "Invented Native Artist")
        let apply = app.buttons["request-intake-apply"]; reveal(apply, in: app); apply.click()
        let message = app.staticTexts["request-intake-message"]
        XCTAssertTrue((message.value as? String ?? message.label).contains("Added 1 reviewed requests"))
        app.buttons["sidebar-crates"].click(); app.buttons["sidebar-planning"].click()
        let advanced = app.buttons["planner-advanced-toggle"]; reveal(advanced, in: app); advanced.click()
        let event = app.textViews["planner-event-json"]; reveal(event, in: app)
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: Data((event.value as? String ?? "").utf8)) as? [String: Any])
        let lists = try XCTUnwrap(object["music_lists"] as? [String: Any])
        let requests = try XCTUnwrap(lists["must_play"] as? [[String: Any]])
        XCTAssertEqual(requests.count, 1)
        XCTAssertEqual(requests.first?["title"] as? String, "Imported Request Fixture")
        XCTAssertEqual(requests.first?["artist"] as? String, "Invented Native Artist")
        reveal(advanced, in: app); advanced.click()
        let generate = app.buttons["planner-generate"]; reveal(generate, in: app); generate.click()
        XCTAssertTrue(app.staticTexts["plan-alternative-0"].waitForExistence(timeout: 20))
        XCTAssertEqual(try Data(contentsOf: fixture), original)
        let shot = XCTAttachment(screenshot: app.windows.firstMatch.screenshot())
        shot.name = "native-request-intake-plan"; shot.lifetime = .keepAlways; add(shot)
    }

    @MainActor
    func testRequestCSVPreviewRejectsUnknownColumnsAndPreservesCategories() throws {
        let app = launchApp()
        app.buttons["sidebar-planning"].click()
        let intake = app.buttons["request-intake-toggle"]; reveal(intake, in: app); intake.click()
        let format = app.popUpButtons["request-intake-format"]; reveal(format, in: app); format.click()
        app.menuItems["CSV with headers"].click()
        let source = app.textViews["request-intake-source"]; reveal(source, in: app)
        replaceText(source, with: "title,unrecognized\nNo,silent discard")
        let preview = app.buttons["request-intake-preview"]; reveal(preview, in: app); preview.click()
        let error = app.staticTexts["request-intake-message"]
        XCTAssertTrue((error.value as? String ?? error.label).contains("Unknown or duplicate columns"))
        XCTAssertFalse(app.buttons["request-intake-apply"].exists)
        reveal(source, in: app)
        replaceText(source, with: "title,artist,version,category\nRequired,Synthetic,Radio,must_play\nExcluded,Synthetic,,do_not_play\nOptional,Synthetic,Live,play_if_possible")
        reveal(preview, in: app); preview.click()
        let count = app.staticTexts["request-intake-count"]
        XCTAssertTrue((count.value as? String ?? count.label).contains("3 requests"))
        let shot = XCTAttachment(screenshot: app.windows.firstMatch.screenshot())
        shot.name = "native-request-intake-review"; shot.lifetime = .keepAlways; add(shot)
        let apply = app.buttons["request-intake-apply"]; reveal(apply, in: app); apply.click()
        let advanced = app.buttons["planner-advanced-toggle"]; reveal(advanced, in: app); advanced.click()
        let event = app.textViews["planner-event-json"]; reveal(event, in: app)
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: Data((event.value as? String ?? "").utf8)) as? [String: Any])
        let lists = try XCTUnwrap(object["music_lists"] as? [String: Any])
        XCTAssertEqual((lists["must_play"] as? [[String: Any]])?.first?["version"] as? String, "Radio")
        XCTAssertEqual((lists["do_not_play"] as? [[String: Any]])?.first?["title"] as? String, "Excluded")
        XCTAssertEqual((lists["play_if_possible"] as? [[String: Any]])?.first?["version"] as? String, "Live")
    }

    @MainActor
    func testVisualFileConstraintsReachPlannerAndPreserveDraft() throws {
        let (fixture, original) = try generatedFixture(title: "Visual Constraint Fixture")
        let app = launchApp()
        try scanAndGenerate(app, fixture: fixture)
        let controls = app.buttons["planner-file-constraints"]; reveal(controls, in: app); controls.click()
        let search = app.textFields["constraint-search"]; reveal(search, in: app); replaceText(search, with: "Visual Constraint Fixture")
        app.buttons["constraint-find"].click()
        let require = app.buttons["constraint-require"]; XCTAssertTrue(require.waitForExistence(timeout: 15)); reveal(require, in: app); require.click()
        let pin = app.buttons["constraint-pin"]; reveal(pin, in: app); pin.click()
        app.buttons["constraint-exclude"].click()
        let conflict = app.staticTexts["constraint-message"]
        XCTAssertTrue((conflict.value as? String ?? conflict.label).contains("required or pinned"))
        app.buttons["sidebar-crates"].click(); app.buttons["sidebar-planning"].click()
        let advanced = app.buttons["planner-advanced-toggle"]; reveal(advanced, in: app); advanced.click()
        let editor = app.textViews["planner-policy-json"]; reveal(editor, in: app)
        let policy = try XCTUnwrap(JSONSerialization.jsonObject(with: Data((editor.value as? String ?? "").utf8)) as? [String: Any])
        let required = try XCTUnwrap(policy["must_include"] as? [String])
        let pins = try XCTUnwrap(policy["pinned"] as? [[String: Any]])
        XCTAssertEqual(required.count, 1)
        XCTAssertEqual(pins.first?["file_id"] as? String, required.first)
        XCTAssertEqual(pins.first?["position"] as? Int, 0)
        reveal(advanced, in: app); advanced.click()
        let generate = app.buttons["planner-generate"]; reveal(generate, in: app); generate.click()
        XCTAssertTrue(app.staticTexts["plan-alternative-0"].waitForExistence(timeout: 20))
        XCTAssertEqual(try Data(contentsOf: fixture), original)
        let shot = XCTAttachment(screenshot: app.windows.firstMatch.screenshot())
        shot.name = "native-visual-file-constraints"; shot.lifetime = .keepAlways; add(shot)
    }

}
