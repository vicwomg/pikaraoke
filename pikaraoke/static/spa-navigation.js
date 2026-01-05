/**
 * SPA (Single Page Application) Navigation System for Pikaraoke
 * Enables dynamic content loading without full page refreshes
 */

(function() {
    'use strict';

    // Configuration
    const config = {
        contentSelector: '.box',
        linkSelector: 'a[href]', // Intercept all links, not just navbar
        notificationSelector: '#notification-alt',
        scrollBehavior: 'smooth'
    };

    // State management
    let isNavigating = false;
    let currentPath = window.location.pathname;
    let loadedResources = new Set(); // Track loaded external resources
    let isInitialized = false; // Prevent multiple initializations

    /**
     * Initialize SPA navigation
     */
    function init() {
        // Prevent double initialization
        if (isInitialized) {
            console.log('SPA Navigation already initialized, skipping');
            return;
        }

        // Handle navigation clicks
        attachNavListeners();

        // Handle browser back/forward buttons
        window.addEventListener('popstate', handlePopState);

        // Ensure hamburger menu works across all pages
        initHamburgerMenu();

        // Initialize username change handler
        initUsernameHandler();

        // Initialize queue management handlers
        initQueueHandlers();

        // Mark initial page load
        if (window.history.state === null) {
            window.history.replaceState({ path: currentPath }, '', currentPath);
        }

        isInitialized = true;
        console.log('SPA Navigation initialized');
    }

    /**
     * Initialize hamburger menu with event delegation
     * This ensures it works reliably across all page transitions
     */
    function initHamburgerMenu() {
        // Remove any existing handlers first to avoid duplicates
        $(document).off('click', '.navbar-burger');

        // Bind with event delegation
        $(document).on('click', '.navbar-burger', function(e) {
            e.preventDefault();
            e.stopPropagation();
            $('.navbar-burger').toggleClass('is-active');
            $('.navbar-menu').toggleClass('is-active');
        });
    }

    /**
     * Initialize username change handler with event delegation
     * This ensures it works reliably across all page transitions
     */
    function initUsernameHandler() {
        // Remove any existing handlers first to avoid duplicates
        $(document).off('click', '#current-user');

        // Bind with event delegation
        $(document).on('click', '#current-user', function(e) {
            e.preventDefault();
            // Get the current name from the cookie dynamically
            let currentName = Cookies.get("user");
            let name = window.prompt(
                "Do you want to change the name of the person using this device? This will show up on queued songs. Current: " + currentName
            );
            // Only update if user clicked OK and entered a non-empty name
            // null = Cancel clicked, "" = OK with empty input
            if (name !== null && name.trim() !== "") {
                Cookies.set("user", name, { expires: 3650 });
                // Update the displayed name without reloading
                $("#current-user span").text(name);
            }
            // Remove focus from the link to prevent CSS focus styling (black background)
            $(this).blur();
        });
    }

    /**
     * Initialize queue management handlers with event delegation
     * This ensures they work reliably across all page transitions
     */
    function initQueueHandlers() {
        // Global flag to prevent rapid up/down clicks
        // Survives DOM regeneration caused by socket updates
        if (typeof window.queueButtonDebouncing === 'undefined') {
            window.queueButtonDebouncing = false;
        }

        // Remove any existing handlers first to avoid duplicates
        $(document).off('click', '.confirm-clear');
        $(document).off('click', '.confirm-delete');
        $(document).off('click', '.up-button');
        $(document).off('click', '.down-button');
        $(document).off('click', '.add-random');

        // Clear queue confirmation
        $(document).on('click', '.confirm-clear', function(e) {
            e.preventDefault();
            let userInput = window.prompt(
                "Are you sure you want to clear the ENTIRE queue? Type 'ok' to continue"
            );
            // Only clear if user typed 'ok' exactly (case insensitive)
            if (userInput !== null && userInput.toLowerCase() === "ok") {
                $.get(this.href);
            }
        });

        // Delete song from queue confirmation
        $(document).on('click', '.confirm-delete', function(e) {
            e.preventDefault();
            if (window.confirm(`Are you sure you want to delete "${this.title}" from the queue?`)) {
                $.get(this.href);
            }
        });

        // Move song up in queue
        $(document).on('click', '.up-button', function(e) {
            e.preventDefault();

            // Check global debounce flag - prevents all up/down clicks during debounce
            if (window.queueButtonDebouncing) {
                return;
            }

            // Set global debounce flag
            window.queueButtonDebouncing = true;

            // Visual feedback on all up/down buttons
            $('.up-button, .down-button').css('pointer-events', 'none').css('opacity', '0.5');

            $.get(this.href).always(function() {
                // Re-enable all buttons after request completes + 500ms
                setTimeout(function() {
                    $('.up-button, .down-button').css('pointer-events', 'auto').css('opacity', '1');
                    window.queueButtonDebouncing = false;
                }, 500);
            });
        });

        // Move song down in queue
        $(document).on('click', '.down-button', function(e) {
            e.preventDefault();

            // Check global debounce flag - prevents all up/down clicks during debounce
            if (window.queueButtonDebouncing) {
                return;
            }

            // Set global debounce flag
            window.queueButtonDebouncing = true;

            // Visual feedback on all up/down buttons
            $('.up-button, .down-button').css('pointer-events', 'none').css('opacity', '0.5');

            $.get(this.href).always(function() {
                // Re-enable all buttons after request completes + 500ms
                setTimeout(function() {
                    $('.up-button, .down-button').css('pointer-events', 'auto').css('opacity', '1');
                    window.queueButtonDebouncing = false;
                }, 500);
            });
        });

        // Add random songs to queue
        $(document).on('click', '.add-random', function(e) {
            e.preventDefault();
            const amount = $('#randomNumberInput').val();
            const baseUrl = '/queue/addrandom';
            $.get(`${baseUrl}?amount=${amount}`);
        });
    }

    /**
     * Attach click listeners to all navigation links
     */
    function attachNavListeners() {
        $(document).on('click', config.linkSelector, function(e) {
            const href = $(this).attr('href');

            // Only intercept internal links that should use SPA navigation
            if (href && !href.startsWith('http') && !href.startsWith('#') && !shouldExcludeLink(this)) {
                e.preventDefault();
                navigateTo(href);
            }
        });
    }

    /**
     * Check if a link should be excluded from SPA navigation
     * Admin actions and system operations should do full page reloads
     * @param {HTMLElement} link - The link element
     * @returns {boolean}
     */
    function shouldExcludeLink(link) {
        const href = $(link).attr('href');
        const $link = $(link);

        // Exclude links with specific classes that use AJAX handlers
        if ($link.hasClass('no-spa') ||
            $link.hasClass('edit-button') ||
            $link.hasClass('add-song-link') ||  // Browse page add to queue
            $link.hasClass('confirm-clear') ||   // Clear queue button (has its own handler)
            $link.hasClass('confirm-delete') ||  // Delete song button (has its own handler)
            $link.hasClass('up-button') ||       // Move song up button (has its own handler)
            $link.hasClass('down-button') ||     // Move song down button (has its own handler)
            $link.hasClass('add-random')) {      // Add random songs button (has its own handler)
            return true;
        }

        // Exclude admin action links that perform system operations
        const excludedPaths = [
            '/quit',
            '/shutdown',
            '/reboot',
            '/logout',
            '/login',
            '/update_ytdl',
            '/refresh',
            '/expand_fs',
            '/clear_preferences',
            '/auth',
            '/batch-song-renamer', // Edit all songs page
            '/files/edit', // Edit single song
            '/files/delete' // Delete song
        ];

        // Check if the href matches any excluded path
        return excludedPaths.some(path => href && href.includes(path));
    }

    /**
     * Navigate to a new page dynamically
     * @param {string} url - The URL to navigate to
     * @param {boolean} addToHistory - Whether to add to browser history
     */
    async function navigateTo(url, addToHistory = true) {
        // Prevent concurrent navigations
        if (isNavigating) {
            return;
        }

        // Don't navigate if already on this page
        if (url === currentPath && addToHistory) {
            return;
        }

        isNavigating = true;

        // Close hamburger menu if open
        $('.navbar-burger').removeClass('is-active');
        $('.navbar-menu').removeClass('is-active');

        try {
            // Fetch the new page content with cache-busting to ensure fresh data
            const cacheBuster = Date.now();
            const separator = url.includes('?') ? '&' : '?';
            const fetchUrl = `${url}${separator}_=${cacheBuster}`;

            const response = await fetch(fetchUrl, {
                method: 'GET',
                headers: {
                    'X-Requested-With': 'XMLHttpRequest',
                    'Accept': 'text/html',
                    'Cache-Control': 'no-cache, no-store, must-revalidate',
                    'Pragma': 'no-cache'
                }
            });

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            const html = await response.text();

            // Parse the HTML response
            const parser = new DOMParser();
            const doc = parser.parseFromString(html, 'text/html');

            // Extract the new content
            const newContent = doc.querySelector(config.contentSelector);
            const newTitle = doc.querySelector('title');
            const newScripts = doc.querySelectorAll('script');
            const newStylesheets = doc.querySelectorAll('link[rel="stylesheet"]');
            const newInlineStyles = doc.querySelectorAll('style');

            if (newContent) {
                // Cleanup old scripts and event handlers
                cleanupOldPage();

                // Update the content
                $(config.contentSelector).html(newContent.innerHTML);

                // Update page title
                if (newTitle) {
                    document.title = newTitle.textContent;
                }

                // Update navigation highlighting
                updateNavHighlight(url);

                // Load external resources (CSS and JS) before executing inline scripts
                await loadExternalResources(newStylesheets, newScripts);

                // Inject inline styles from the new page
                injectInlineStyles(newInlineStyles);

                // Execute page-specific inline scripts
                executeScripts(newScripts);

                // Scroll to top
                window.scrollTo({ top: 0, behavior: config.scrollBehavior });

                // Update browser history
                if (addToHistory) {
                    window.history.pushState({ path: url }, '', url);
                }

                // Update current path
                currentPath = url;

                // Show success notification (optional, can be commented out)
                // showNotification('Page loaded', 'is-success', 500);

            } else {
                console.error('Could not find content container in response');
                // Fallback to normal navigation
                window.location.href = url;
            }

        } catch (error) {
            console.error('Navigation error:', error);
            // Fallback to normal navigation on error
            window.location.href = url;
        } finally {
            isNavigating = false;
        }
    }

    /**
     * Handle browser back/forward button
     * @param {PopStateEvent} event
     */
    function handlePopState(event) {
        if (event.state && event.state.path) {
            navigateTo(event.state.path, false);
        } else {
            // Fallback to full page reload if no state
            window.location.reload();
        }
    }

    /**
     * Update navbar active state highlighting
     * @param {string} url - The current URL (may include query params)
     */
    function updateNavHighlight(url) {
        // Extract base path without query parameters
        const path = url.split('?')[0];

        // Remove all active classes
        $('.navbar-item').removeClass('is-active');

        // Add active class to matching navbar item
        if (path === '/') {
            $('#home').addClass('is-active');
        } else if (path === '/queue') {
            $('#queue').addClass('is-active');
        } else if (path === '/search') {
            $('#search').addClass('is-active');
        } else if (path === '/browse' || path.startsWith('/browse')) {
            $('#browse').addClass('is-active');
        } else if (path === '/info') {
            $('#info').addClass('is-active');
        }
    }

    /**
     * Load external resources (CSS and JS files) from the new page
     * @param {NodeList} stylesheets - Link elements for stylesheets
     * @param {NodeList} scripts - Script elements
     * @returns {Promise} Resolves when all resources are loaded
     */
    async function loadExternalResources(stylesheets, scripts) {
        const loadPromises = [];

        // Load stylesheets
        stylesheets.forEach(link => {
            const href = link.getAttribute('href');
            if (href && !isResourceLoaded(href)) {
                loadPromises.push(loadStylesheet(href));
            }
        });

        // Load external scripts
        scripts.forEach(script => {
            const src = script.getAttribute('src');
            if (src && !isResourceLoaded(src)) {
                loadPromises.push(loadScript(src));
            }
        });

        // Wait for all resources to load
        await Promise.all(loadPromises);
    }

    /**
     * Check if a resource is already loaded
     * @param {string} url - The resource URL
     * @returns {boolean}
     */
    function isResourceLoaded(url) {
        // Normalize URL for comparison
        const normalizedUrl = url.split('?')[0]; // Remove query strings for comparison

        // Check if already tracked
        if (loadedResources.has(normalizedUrl)) {
            return true;
        }

        // Check if stylesheet already exists in DOM
        const existingStylesheet = document.querySelector(`link[href*="${normalizedUrl}"]`);
        if (existingStylesheet) {
            loadedResources.add(normalizedUrl);
            return true;
        }

        // Check if script already exists in DOM
        const existingScript = document.querySelector(`script[src*="${normalizedUrl}"]`);
        if (existingScript) {
            loadedResources.add(normalizedUrl);
            return true;
        }

        return false;
    }

    /**
     * Load a stylesheet dynamically
     * @param {string} href - The stylesheet URL
     * @returns {Promise}
     */
    function loadStylesheet(href) {
        return new Promise((resolve, reject) => {
            const link = document.createElement('link');
            link.rel = 'stylesheet';
            link.href = href;
            link.onload = () => {
                loadedResources.add(href.split('?')[0]);
                resolve();
            };
            link.onerror = () => {
                console.error(`Failed to load stylesheet: ${href}`);
                reject(new Error(`Failed to load stylesheet: ${href}`));
            };
            document.head.appendChild(link);
        });
    }

    /**
     * Load a script dynamically
     * @param {string} src - The script URL
     * @returns {Promise}
     */
    function loadScript(src) {
        return new Promise((resolve, reject) => {
            const script = document.createElement('script');
            script.src = src;
            script.onload = () => {
                loadedResources.add(src.split('?')[0]);
                resolve();
            };
            script.onerror = () => {
                console.error(`Failed to load script: ${src}`);
                reject(new Error(`Failed to load script: ${src}`));
            };
            document.head.appendChild(script);
        });
    }

    /**
     * Inject inline styles from the new page
     * @param {NodeList} styles - Style elements to inject
     */
    function injectInlineStyles(styles) {
        // Remove previously injected page-specific styles
        document.querySelectorAll('style[data-spa-injected]').forEach(style => {
            style.remove();
        });

        // Inject new inline styles
        styles.forEach(styleElement => {
            if (styleElement.textContent) {
                const newStyle = document.createElement('style');
                newStyle.textContent = styleElement.textContent;
                newStyle.setAttribute('data-spa-injected', 'true');
                document.head.appendChild(newStyle);
            }
        });
    }

    /**
     * Execute scripts from the new page
     * @param {NodeList} scripts - Script elements to execute
     */
    function executeScripts(scripts) {
        scripts.forEach(script => {
            // Only execute inline scripts
            // External scripts have already been loaded by loadExternalResources
            if (script.textContent && !script.src) {
                try {
                    // Create a new script element to ensure execution
                    const newScript = document.createElement('script');
                    newScript.textContent = script.textContent;

                    // Execute the script
                    document.body.appendChild(newScript);

                    // Clean up immediately
                    document.body.removeChild(newScript);
                } catch (error) {
                    console.error('Error executing script:', error);
                }
            }
        });
    }

    /**
     * Cleanup old page resources before loading new content
     */
    function cleanupOldPage() {
        // Remove old event handlers on elements that will be replaced
        $(config.contentSelector).off();

        // Note: We don't disconnect socket.io as it should persist across page changes
        // The socket connection is maintained globally
    }

    /**
     * Show a notification message
     * @param {string} message
     * @param {string} categoryClass
     * @param {number} timeout
     */
    function showNotification(message, categoryClass, timeout = 3000) {
        const notification = $(config.notificationSelector);
        notification.addClass(categoryClass);
        notification.find('div').text(message);
        notification.fadeIn();

        setTimeout(function() {
            notification.fadeOut();
        }, timeout);

        setTimeout(function() {
            notification.removeClass(categoryClass);
        }, timeout + 750);
    }

    // Initialize when DOM is ready
    $(document).ready(function() {
        init();
    });

})();
