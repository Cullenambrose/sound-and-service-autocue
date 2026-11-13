import AppKit
import Darwin
import Foundation
import WebKit

final class StudioApp: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate,
                       WKScriptMessageHandler, WKDownloadDelegate {
    private var window: NSWindow!
    private var webView: WKWebView!
    private var backend: Process?
    private var lockFD: Int32 = -1
    private var origin: URL?
    private var started = false
    private var closing = false
    private var attempts = 0
    private var dataPath = ""

    func applicationDidFinishLaunching(_ notification: Notification) {
        do {
            let support: URL
            if let override = ProcessInfo.processInfo.environment["AUTOCUE_DESKTOP_DATA"], !override.isEmpty {
                guard override.hasPrefix("/") else { throw NSError(domain: "data", code: 1) }
                support = URL(fileURLWithPath: override, isDirectory: true)
            } else {
                support = FileManager.default.homeDirectoryForCurrentUser
                    .appendingPathComponent("Library/Application Support/AutoCue Studio", isDirectory: true)
            }
            try FileManager.default.createDirectory(at: support, withIntermediateDirectories: true)
            dataPath = support.resolvingSymlinksInPath().path
            lockFD = open((dataPath as NSString).appendingPathComponent(".desktop.lock"),
                          O_CREAT | O_RDWR | O_NOFOLLOW, S_IRUSR | S_IWUSR)
            guard lockFD >= 0, flock(lockFD, LOCK_EX | LOCK_NB) == 0 else {
                fail("Another desktop instance is using this Studio data directory.")
                return
            }
            setupWindow()
            launchBackend()
        } catch {
            fail("Could not initialize the Studio data directory.")
        }
    }

    private func setupWindow() {
        let controller = WKUserContentController()
        controller.add(self, name: "selectMusicFolder")
        let script = """
        (() => {
          const field = document.getElementById('root');
          if (!field) return;
          const button = document.createElement('button');
          button.type = 'button';
          button.textContent = 'Choose music folder…';
          button.addEventListener('click', () =>
            window.webkit.messageHandlers.selectMusicFolder.postMessage('choose'));
          field.insertAdjacentElement('afterend', button);
        })();
        """
        controller.addUserScript(WKUserScript(source: script, injectionTime: .atDocumentEnd,
                                             forMainFrameOnly: true))
        let configuration = WKWebViewConfiguration()
        configuration.userContentController = controller
        webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1200, height: 850),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "AutoCue Studio"
        window.contentView = webView
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    private func freePort() throws -> UInt16 {
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        guard fd >= 0 else { throw NSError(domain: "port", code: 1) }
        defer { close(fd) }
        var address = sockaddr_in()
        address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        address.sin_family = sa_family_t(AF_INET)
        address.sin_addr = in_addr(s_addr: in_addr_t(INADDR_LOOPBACK).bigEndian)
        let bound = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.bind(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        guard bound == 0 else { throw NSError(domain: "port", code: 2) }
        var size = socklen_t(MemoryLayout<sockaddr_in>.size)
        let result = withUnsafeMutablePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { getsockname(fd, $0, &size) }
        }
        guard result == 0 else { throw NSError(domain: "port", code: 3) }
        return UInt16(bigEndian: address.sin_port)
    }

    private func launchBackend() {
        attempts += 1
        do {
            let port = try freePort()
            let base = URL(string: "http://127.0.0.1:\(port)")!
            let executable = Bundle.main.bundleURL.appendingPathComponent("Contents/Resources/backend/studio-backend")
            let process = Process()
            process.executableURL = executable
            process.arguments = ["--data", dataPath, "--port", String(port)]
            var environment = ProcessInfo.processInfo.environment
            for key in environment.keys where key.hasPrefix("PYTHON") || key.hasPrefix("AUTOCUE_") {
                environment.removeValue(forKey: key)
            }
            environment["AUTOCUE_DESKTOP_PARENT"] = String(getpid())
            process.environment = environment
            process.currentDirectoryURL = URL(fileURLWithPath: dataPath, isDirectory: true)
            let output = Pipe()
            process.standardOutput = output
            process.standardError = FileHandle.nullDevice
            backend = process
            var buffer = Data()
            output.fileHandleForReading.readabilityHandler = { [weak self, weak process] handle in
                let bytes = handle.availableData
                if bytes.isEmpty { handle.readabilityHandler = nil; return }
                buffer.append(bytes)
                guard buffer.count < 8192 else { handle.readabilityHandler = nil; return }
                let text = String(decoding: buffer, as: UTF8.self)
                if text.contains("AutoCue Studio alpha: \(base.absoluteString)\n") {
                    handle.readabilityHandler = { handle in
                        if handle.availableData.isEmpty { handle.readabilityHandler = nil }
                    }
                    DispatchQueue.main.async {
                        guard let self, let process, self.backend === process, process.isRunning else { return }
                        self.checkReady(base, process: process, remaining: 50)
                    }
                }
            }
            process.terminationHandler = { [weak self, weak process] _ in
                DispatchQueue.main.async {
                    guard let self, let process, self.backend === process, !self.closing else { return }
                    if !self.started && self.attempts < 3 { self.launchBackend() }
                    else { self.fail("The local Studio backend stopped unexpectedly.") }
                }
            }
            try process.run()
            DispatchQueue.main.asyncAfter(deadline: .now() + 30) { [weak self, weak process] in
                guard let self, let process, self.backend === process, !self.started, !self.closing else { return }
                self.fail("The local Studio backend did not become ready.")
            }
        } catch {
            fail("Could not start the bundled Studio backend.")
        }
    }

    private func checkReady(_ base: URL, process: Process, remaining: Int) {
        var request = URLRequest(url: base.appendingPathComponent("api/session"))
        request.timeoutInterval = 1
        URLSession.shared.dataTask(with: request) { [weak self, weak process] data, response, _ in
            DispatchQueue.main.async {
                guard let self, let process, self.backend === process, process.isRunning, !self.closing else { return }
                if let data, let http = response as? HTTPURLResponse, http.statusCode == 200,
                   let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                   json["token"] is String {
                    self.origin = base
                    self.started = true
                    self.webView.load(URLRequest(url: base))
                } else if remaining > 0 {
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) {
                        self.checkReady(base, process: process, remaining: remaining - 1)
                    }
                } else {
                    self.fail("The local Studio backend did not respond.")
                }
            }
        }.resume()
    }

    private func local(_ url: URL?) -> Bool {
        guard let url, let origin else { return false }
        return url.scheme == "http" && url.host == "127.0.0.1" && url.port == origin.port
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.name == "selectMusicFolder", message.frameInfo.isMainFrame,
              local(message.frameInfo.request.url), message.body as? String == "choose",
              local(webView.url) else { return }
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.beginSheetModal(for: window) { [weak self] result in
            guard let self, result == .OK, let url = panel.url, self.local(self.webView.url),
                  let encoded = try? JSONSerialization.data(withJSONObject: [url.path]),
                  let json = String(data: encoded, encoding: .utf8) else { return }
            self.webView.evaluateJavaScript("document.getElementById('root').value = \(json)[0]")
        }
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url else { decisionHandler(.cancel); return }
        if action.shouldPerformDownload {
            let trustedBlob = url.scheme == "blob" && local(action.sourceFrame.request.url)
            decisionHandler((local(url) || trustedBlob) ? .download : .cancel)
            return
        }
        if local(url) && action.targetFrame?.isMainFrame == true {
            decisionHandler(.allow)
        } else {
            if action.navigationType == .linkActivated, ["http", "https"].contains(url.scheme?.lowercased() ?? ""),
               !local(url) { NSWorkspace.shared.open(url) }
            decisionHandler(.cancel)
        }
    }

    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        guard let url = response.response.url,
              local(url) || (url.scheme == "blob" && local(webView.url)) else {
            decisionHandler(.cancel)
            return
        }
        decisionHandler(response.canShowMIMEType ? .allow : .download)
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) {
        download.delegate = self
    }

    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) {
        download.delegate = self
    }

    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse,
                  suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = URL(fileURLWithPath: suggestedFilename).lastPathComponent
        panel.beginSheetModal(for: window) { result in completionHandler(result == .OK ? panel.url : nil) }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationWillTerminate(_ notification: Notification) {
        closing = true
        if let process = backend, process.isRunning {
            process.terminate()
            let deadline = Date().addingTimeInterval(3)
            while process.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.05) }
            if process.isRunning { kill(process.processIdentifier, SIGKILL) }
            process.waitUntilExit()
        }
        if lockFD >= 0 { close(lockFD); lockFD = -1 }
    }

    private func fail(_ message: String) {
        let alert = NSAlert()
        alert.messageText = "AutoCue Studio could not start"
        alert.informativeText = message
        alert.runModal()
        NSApp.terminate(nil)
    }
}

let app = NSApplication.shared
let delegate = StudioApp()
app.delegate = delegate
app.setActivationPolicy(.regular)
let menu = NSMenu()
let appMenuItem = NSMenuItem()
menu.addItem(appMenuItem)
let appMenu = NSMenu()
appMenu.addItem(withTitle: "Quit AutoCue Studio", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
appMenuItem.submenu = appMenu
app.mainMenu = menu
app.run()
