from playwright.async_api import async_playwright
import asyncio
import configparser
import os
from dataclasses import dataclass, asdict
from typing import Optional, Any
import time
import json

# Read configuration from config.ini
config = configparser.ConfigParser()
config.read('config.ini')

# define constant variable
URL = config['WebScraping']['URL']

# Authentication 
USERNAME = config['Authentication']['USERNAME']
PASSWORD = config['Authentication']['PASSWORD']
AUTHENTICATION_URL = config['WebScraping']['AUTHENTICATION_URL']

# Settings
HEADLESS = config.getboolean('Settings', 'HEADLESS', fallback=True)
TIMEOUT = int(config.get('Settings', 'TIMEOUT', fallback='30000'))  # in milliseconds
SCREENSHOT_DIR = config.get('Settings', 'SCREENSHOT_DIR', fallback='screenshots')
AUTH_STATE_PATH = config['Settings']['AUTH_STATE_PATH']
AUTH_MAX_AGE = int(config['Settings']['AUTH_MAX_AGE_HOURS']) * 3600 
MEANINGFUL_IMAGE_FILE = config['Settings'].get('MEANINGFUL_IMAGE_FILE', 'meaningful_images.json')
ALL_IMAGES_FILE = config['Settings'].get('ALL_IMAGES_FILE', 'all_images.json')
SVGS_FILE = config['Settings'].get('SVG_FILE', 'svgs.json')

@dataclass
class ScrapedImage:
    """Structured representation of a scraped image"""
    src: str
    url: str
    alt: Optional[str] = None

    # header and footer flags
    is_header: bool = False
    is_footer: bool = False
    is_logo: bool = False

    # image size flags
    file_size: int = 0
    is_large_file: bool = False

    # image size flags
    width: Optional[int] = None
    height: Optional[int] = None
    has_image_shape: bool = False

    # flags for meaningful image
    is_meaningful: bool = False
    screenshot_path: Optional[str] = None
    
    def to_dict(self):
        """Convert to dictionary for JSON serialization"""
        return asdict(self)

@dataclass
class ScrapedSVG:
    """Structured representation of a scraped SVG"""
    url: str
    is_inline: bool = False  # Whether it's an inline SVG or external file
    content: Optional[str] = None  # For inline SVGs, store the content
    src: Optional[str] = None  # For SVGs loaded via img, object, etc.
    
    # Size properties
    width: Optional[int] = None
    height: Optional[int] = None
    
    # Position flags
    is_header: bool = False
    is_footer: bool = False
    is_logo: bool = False
    
    # Screenshot if taken
    screenshot_path: Optional[str] = None
    
    def to_dict(self):
        """Convert to dictionary for JSON serialization"""
        return asdict(self)

async def authenticate():
    """Authenticate and Return persistent context"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS, timeout=TIMEOUT)

        # Check if auth state exists and is not too old
        auth_state_valid = False
        if os.path.exists(AUTH_STATE_PATH):
            # Check if file is not too old
            file_age = time.time() - os.path.getmtime(AUTH_STATE_PATH)
            if file_age < AUTH_MAX_AGE:
                print("Using existing authentication state")
                auth_state_valid = True
            else:
                print(f"Auth state is too old ({file_age/3600:.1f} hours), re-authenticating")

        # Create browser context with auth state if valid
        if auth_state_valid:
            context = await browser.new_context(storage_state=AUTH_STATE_PATH)

            # Test auth state by visiting a protected page
            page = await context.new_page()
            await page.goto(URL)  # Use your main URL instead of auth URL
            
            # Check if we're still logged in
            is_logged_in = await page.evaluate("""() => {
                // Add logic to check if we're logged in
                // Example: return document.querySelector('.user-avatar') !== null;
                // Adjust this selector based on your site
                return !document.querySelector('.login-button');
            }""")
            
            await page.close()
            
            if is_logged_in:
                print("Authentication state is valid")
                return context
            else:
                print("Auth state exists but session expired, re-authenticating")

        # If we get here, we need to authenticate
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto(AUTHENTICATION_URL)

        # Authentication for Reddit
        try:
            # Handle cookie consent if it appears
            # try:
            #     await page.click('button[data-cookie-consent-accept-all]', timeout=5000)
            # except Exception as e:
            #     print("No cookie consent button found or timeout occurred:", e)

            # Authentication for GitHub
            # await page.get_by_label("Username or email address").fill(USERNAME)
            # await page.get_by_label("Password").fill(PASSWORD)
            # await page.get_by_role("button", name="Sign in").click()
            
            # Authentication for Stack Overflow 
            # await page.fill('#email', USERNAME)
            # await page.fill('#password', PASSWORD)
            # await page.click('#submit-button')
            await page.fill('input[name="username"]', USERNAME)
            await page.fill('input[name="password"]', PASSWORD)
            await page.click('button[type="button"]')

            # wait for authentication to complete
            await page.wait_for_load_state("networkidle")
            
            # Optional: check if login was successful
            await asyncio.sleep(2)  # Short wait to ensure page updates
            
            # Save authentication state
            await context.storage_state(path=AUTH_STATE_PATH)
            print("New authentication completed and state saved")
            
            await page.close()
            return context
            
        except Exception as e:
            print(f"Authentication error: {str(e)}")
            await browser.close()
            raise

async def is_header_footer_logo(image: Any):
    try:
        # Check if image is in header/footer by position
        position_check = await image.evaluate("""img => {
            const rect = img.getBoundingClientRect();
            const documentHeight = document.documentElement.scrollHeight;
            const viewportHeight = window.innerHeight;

            // Check if image is in top 10% or bottom 10% of the page
            const isTopSection = rect.top < viewportHeight * 0.1;
            const isBottomSection = rect.bottom > documentHeight - viewportHeight * 0.1;

            return { isTopSection, isBottomSection };
        }""")
        is_header = position_check['isTopSection']
        is_footer = position_check['isBottomSection']
        if is_header or is_footer:
            return is_header, is_footer, True
        return is_header, is_footer, False
    except Exception as e:
        print(f"Error checking header/footer position for image: {str(e)}")
        return None, None, False

async def check_file_image_size(page: Any, src: Any):
    try:
        # Make a HEAD request to get Content-Length without downloading image
        response_size = await page.evaluate("""async (url) => {
            try {
                const response = await fetch(url, { method: 'HEAD' });
                return parseInt(response.headers.get('Content-Length') || '0');
            } catch (e) {
                return 0;
            }
        }""", src)
        
        # Images larger than 10KB are more likely to be content
        return response_size, response_size > 10240
    except Exception as e:
        print(f"Error checking image size for {src}: {str(e)}")
        return None, False

async def check_image_size(image: Any):
    width = await image.get_attribute("width")
    height = await image.get_attribute("height")

    if not width or not height:
        return None, None, False
    try:
        width = int(width)
        height = int(height)
    except (ValueError, TypeError):
        return None, None, False

    if width > 100 and height > 100:
        return width, height, True
    return None, None, False

async def is_meaningful_image(image: Any, page: Any):
    # Get source attribute
    src = await image.get_attribute("src")
    if not src:
        return None
    
    # get alt text
    alt = await image.get_attribute("alt")
    # create initial image object
    scraped_image = ScrapedImage(src=src, url=page.url, alt=alt)

    # check footer and header images (logo, banner, etc):
    is_header, is_footer, is_logo = await is_header_footer_logo(image)
    scraped_image.is_header = is_header
    scraped_image.is_footer = is_footer
    scraped_image.is_logo = is_logo

    # check image size
    file_size, is_large_file = await check_file_image_size(page, src)
    scraped_image.file_size = file_size
    scraped_image.is_large_file = is_large_file
    # check image size
    width, height, has_image_shape = await check_image_size(image)
    scraped_image.width = width
    scraped_image.height = height
    scraped_image.has_image_shape = has_image_shape

    # combine all checks and set the is_meaningful flag
    scraped_image.is_meaningful = not is_logo and is_large_file and has_image_shape
    
    # Return the ScrapedImage object, not just a boolean
    return scraped_image

async def save_image_screenshot(page, image_data, index):
    """Save a screenshot of a meaningful image"""
    try:
        # Create a unique filename
        image_url = image_data.src
        filename = f"{index}_{os.path.basename(image_url.split('?')[0])}"
        # Handle case where URL doesn't have a file extension
        if '.' not in filename:
            filename += ".png"
        # Ensure filename is valid
        filename = ''.join(c for c in filename if c.isalnum() or c in '._-')[:100]
        filepath = os.path.join(SCREENSHOT_DIR, filename)
        
        # Method 1: Try to screenshot the element directly
        try:
            # Find the image again in the current page (the element might be stale)
            new_image = await page.query_selector(f"img[src='{image_data.src}']")
            if new_image:
                await new_image.screenshot(path=filepath)
                print(f"Saved screenshot of image to {filepath}")
                return filepath
        except Exception as e:
            print(f"Could not screenshot element directly: {e}")
        
        # Method 2: Navigate to image URL directly if it's a full URL
        if image_data.src.startswith(('http://', 'https://')):
            try:
                # Create a new page to avoid disrupting the current page
                image_page = await page.context.new_page()
                await image_page.goto(image_data.src, timeout=10000)
                await image_page.screenshot(path=filepath)
                await image_page.close()
                print(f"Saved screenshot by navigating to image URL: {filepath}")
                return filepath
            except Exception as e:
                print(f"Could not navigate to image URL: {e}")
        
        return None
    except Exception as e:
        print(f"Error saving image screenshot: {e}")
        return None

async def extract_svgs(page):
    """Extract all SVG elements from the page"""
    svgs = []
    
    # 1. Find inline SVG elements
    inline_svgs = await page.query_selector_all("svg")
    for i, svg in enumerate(inline_svgs):
        try:
            # Get SVG dimensions
            width = await svg.get_attribute("width")
            height = await svg.get_attribute("height")
            width = int(width) if width and width.isdigit() else None
            height = int(height) if height and height.isdigit() else None
            
            # Get outer HTML content
            content = await svg.evaluate("svg => svg.outerHTML")
            
            # Check if in header/footer
            position_data = await svg.evaluate("""svg => {
                const rect = svg.getBoundingClientRect();
                const documentHeight = document.documentElement.scrollHeight;
                const viewportHeight = window.innerHeight;
                
                return {
                    isHeader: rect.top < viewportHeight * 0.1,
                    isFooter: rect.bottom > documentHeight - viewportHeight * 0.1,
                    isLogo: rect.width < 200 && rect.height < 100 && 
                           (rect.top < viewportHeight * 0.2 || 
                            rect.left < viewportHeight * 0.2)
                };
            }""")
            
            # Create SVG object
            svg_obj = ScrapedSVG(
                url=page.url,
                is_inline=True,
                content=content,
                width=width,
                height=height,
                is_header=position_data["isHeader"],
                is_footer=position_data["isFooter"],
                is_logo=position_data["isLogo"]
            )
            
            # Take screenshot if possible
            try:
                os.makedirs(SCREENSHOT_DIR, exist_ok=True)
                filename = f"inline_svg_{i}.png"
                filepath = os.path.join(SCREENSHOT_DIR, filename)
                await svg.screenshot(path=filepath)
                svg_obj.screenshot_path = filepath
            except Exception as e:
                print(f"Could not screenshot SVG: {e}")
            
            svgs.append(svg_obj)
        except Exception as e:
            print(f"Error processing inline SVG: {e}")
    
    # 2. Find SVGs loaded via img tags
    svg_images = await page.query_selector_all("img[src$='.svg']")
    for i, img in enumerate(svg_images):
        try:
            src = await img.get_attribute("src")
            if not src:
                continue
                
            # Get dimensions
            width = await img.get_attribute("width")
            height = await img.get_attribute("height")
            width = int(width) if width and width.isdigit() else None
            height = int(height) if height and height.isdigit() else None
            
            # Check position
            is_header, is_footer, is_logo = await is_header_footer_logo(img)
            
            # Create SVG object
            svg_obj = ScrapedSVG(
                url=page.url,
                is_inline=False,
                src=src,
                width=width,
                height=height,
                is_header=is_header,
                is_footer=is_footer,
                is_logo=is_logo
            )
            
            # Take screenshot
            try:
                os.makedirs(SCREENSHOT_DIR, exist_ok=True)
                filename = f"svg_img_{i}.png"
                filepath = os.path.join(SCREENSHOT_DIR, filename)
                await img.screenshot(path=filepath)
                svg_obj.screenshot_path = filepath
            except Exception as e:
                print(f"Could not screenshot SVG image: {e}")
            
            svgs.append(svg_obj)
        except Exception as e:
            print(f"Error processing SVG image: {e}")
    
    # 3. Find SVGs in object/embed tags
    svg_objects = await page.query_selector_all("object[type='image/svg+xml'], embed[type='image/svg+xml']")
    for i, obj in enumerate(svg_objects):
        try:
            src = await obj.get_attribute("data") or await obj.get_attribute("src")
            if not src:
                continue
                
            # Create SVG object
            svg_obj = ScrapedSVG(
                url=page.url,
                is_inline=False,
                src=src,
                is_header=False,  # Would need additional checks
                is_footer=False,
                is_logo=False
            )
            
            svgs.append(svg_obj)
        except Exception as e:
            print(f"Error processing SVG object: {e}")
    
    print(f"Found {len(svgs)} SVG elements on the page")
    return svgs

async def extract_images(context: Any, url: str):
    page = await context.new_page()
    try:
        await page.goto(url, timeout=30000)
        await page.wait_for_load_state("load")

        images = await page.query_selector_all("img")
        print(f"Found {len(images)} images on page {url}")

        all_images = []
        meaningful_images = []
        for image in images:
            image_data = await is_meaningful_image(image, page)
            if not image_data:
                continue
            all_images.append(image_data)
            
            # Filter for meaningful images
            if image_data.is_meaningful:
                # Take a screenshot of the meaningful image
                screenshot_path = await save_image_screenshot(page, image_data, len(meaningful_images))
                
                # Add screenshot path to image data if successful
                if screenshot_path:
                    image_data.screenshot_path = screenshot_path
                
                meaningful_images.append(image_data)
                print(f"Found meaningful image: {image_data.src[:50]}...")

        # Extract SVGs
        svgs = await extract_svgs(page)
        
        print(f"Found {len(meaningful_images)} meaningful images and {len(svgs)} SVGs on {url}")
        return meaningful_images, all_images, svgs
    except Exception as e:
        print(f"Error processing page {url}: {str(e)}")
        return []
    finally:
        await page.close()

async def crawl_links(links: list[str]):
    async with async_playwright() as p:
        try:
            # Launch a new browser instance
            browser = await p.chromium.launch(headless=HEADLESS, timeout=TIMEOUT)
        
            # Create a new browser context
            context = await browser.new_context(
                storage_state=AUTH_STATE_PATH  # Load the authentication state
            )

            # Create screenshot directory
            os.makedirs(SCREENSHOT_DIR, exist_ok=True)
            
            all_meaningful_images = []
            all_images = []
            all_svgs = []
            for i, link in enumerate(links):
                if i < 10:  # Limit to first 10 links
                    print(f"Processing link {i+1}/{len(links)}: {link}")
                    
                    # Extract images from the page
                    meaningful_images, images, svgs = await extract_images(context, link)
                    all_meaningful_images.extend(meaningful_images)
                    all_images.extend(images)
                    all_svgs.extend(svgs)

                    # Add a small delay to avoid overwhelming the server
                    await asyncio.sleep(1)
                else:
                    break
            print(f"Total meaningful images found: {len(all_meaningful_images)}")
            
            # Convert to dictionaries for JSON serialization
            meaningful_images_dict = [img.to_dict() for img in all_meaningful_images]
            all_images_dict = [img.to_dict() for img in all_images]
            all_svgs_dict = [svg.to_dict() for svg in all_svgs]

           # Save results to a file
            with open(MEANINGFUL_IMAGE_FILE, "w") as f:
                json.dump(meaningful_images_dict, f, indent=2)
            
             # Save results to a file
            with open(ALL_IMAGES_FILE, "w") as f:
                json.dump(all_images_dict, f, indent=2)
            
            with open(SVGS_FILE, "w") as f:
                json.dump(all_svgs_dict, f, indent=2)
            
            return all_meaningful_images
        finally:
            await browser.close()

async def main():
    await authenticate()
    
    link = URL
    await crawl_links([link])

asyncio.run(main())