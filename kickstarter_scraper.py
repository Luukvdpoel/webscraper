from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException
import logging

import math
import re
import html
import json
from datetime import datetime
import os
import time
import csv

from selenium import webdriver
import undetected_chromedriver.v2 as uc

def init_browser(headless=True):
    options = uc.ChromeOptions()
    if headless:
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1200,800")

    print(f"Using uc version: {uc.__version__}")
    driver = uc.Chrome(options=options)
    return driver




def accept_cookies(driver):
    """Handle cookie consent banner if it appears."""
    try:
        # Wait for any cookie banner to load (if present)
        WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.XPATH, "//button[contains(translate(., 'ACEPT', 'acept'), 'accept') or contains(., 'Accept')]"))
        )
    except TimeoutException:
        return  # No banner showed up within 5 seconds
    # If we found a banner, attempt to click the accept button
    try:
        # Some cookie banners might be inside iframes, so check for iframes
        frames = driver.find_elements(By.TAG_NAME, "iframe")
        for frame in frames:
            driver.switch_to.frame(frame)
            try:
                btn = driver.find_element(By.XPATH, "//button[contains(translate(., 'ACEPT', 'acept'), 'accept') or contains(., 'Accept')]")
                btn.click()
                logging.info("Accepted cookies via iframe.")
                driver.switch_to.default_content()
                return
            except NoSuchElementException:
                driver.switch_to.default_content()
                continue
        # If not in an iframe, look in main page
        btn = driver.find_element(By.XPATH, "//button[contains(translate(., 'ACEPT', 'acept'), 'accept') or contains(., 'Accept')]")
        btn.click()
        logging.info("Accepted cookies on main page.")
    except Exception as e:
        logging.warning(f"Cookie banner found but clicking failed: {e}")

def get_project_data(driver):
    """Extract all required data from the current Kickstarter project page."""
    data = {}
    # Wait for a key element from funding stats to ensure page loaded (e.g., backers count via itemprop)
    WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, 'data[itemprop="Project[backers_count]"]'))
    )
    # Project Title
    try:
        title_elem = driver.find_element(By.CSS_SELECTOR, '[data-test-id="project-title"], .type-24')  # CSS may vary; use known data-test or fallback class
        data['Project Title'] = title_elem.text.strip()
    except NoSuchElementException:
        data['Project Title'] = ""  # if not found, leave empty (should not happen if page loaded)
    # Category and Location
    data['Category'] = ""
    data['Location'] = ""
    try:
        # Kickstarter category link contains '/discover/categories'
        cat_elem = driver.find_element(By.XPATH, "//a[contains(@href, '/discover/categories/')]")
        data['Category'] = cat_elem.text.strip()
        # The parent element may contain location text alongside category
        parent_text = cat_elem.find_element(By.XPATH, "./..").text.strip()
        # Remove category text and "Project We Love" if present to isolate location
        loc_text = parent_text
        # Remove "Project We Love" phrase if it exists
        loc_text = loc_text.replace("Project We Love", "").strip()
        # Remove category name from the text
        if data['Category'] and data['Category'] in loc_text:
            loc_text = loc_text.replace(data['Category'], "").strip()
        # After removal, what's left is likely location (e.g., "City, ST")
        # Clean up any stray punctuation or separators
        loc_text = loc_text.strip("· ").strip()
        data['Location'] = loc_text
    except NoSuchElementException:
        logging.warning("Category/location element not found on page.")
    # Use microdata to get numeric stats
    try:
        goal_elem = driver.find_element(By.CSS_SELECTOR, 'data[itemprop="Project[goal]"]')
        pledged_elem = driver.find_element(By.CSS_SELECTOR, 'data[itemprop="Project[pledged]"]')
        backers_elem = driver.find_element(By.CSS_SELECTOR, 'data[itemprop="Project[backers_count]"]')
        goal = goal_elem.get_attribute("value")
        pledged = pledged_elem.get_attribute("value")
        backers = backers_elem.get_attribute("value")
        # Convert to int (the values are likely strings of numbers)
        data['Goal'] = int(math.floor(float(goal))) if goal else 0
        data['Pledged'] = int(math.floor(float(pledged))) if pledged else 0
        data['Backers'] = int(backers) if backers else 0
    except NoSuchElementException:
        # Fallback: parse from visible text if microdata not found
        stats_text = driver.find_element(By.XPATH, "//*[contains(text(),'pledged of')]").text
        # Example format: "$5,000 pledged of $10,000 goal 123 backers"
        # Use regex to extract numbers
        m = re.search(r'([0-9,.]+)\s+pledged of\s+([0-9,.]+)\s+goal\s+([\d,]+)\s+backers', stats_text)
        if m:
            data['Pledged'] = int(m.group(1).replace(",", ""))
            data['Goal'] = int(m.group(2).replace(",", ""))
            data['Backers'] = int(m.group(3).replace(",", ""))
        else:
            logging.error("Failed to parse funding stats text.")
            data.setdefault('Goal', 0); data.setdefault('Pledged', 0); data.setdefault('Backers', 0)
    # Determine currency – if currency code not directly available, infer from pledge text symbol
    data['Currency'] = ""
    try:
        # The pledge element's text contains currency symbol/code (like "$" or "US$")
        pledge_text_full = driver.find_element(By.XPATH, "//*[contains(text(),'pledged of')]").text
        # Match currency symbol or code at start of pledged amount
        cur_match = re.match(r'([^\d\s]+)', pledge_text_full.strip())
        if cur_match:
            cur_symbol = cur_match.group(1)
            # Normalize common symbols to codes if needed (optional mapping)
            currency_map = {"$": "USD", "CA$": "CAD", "US$": "USD", "£": "GBP", "€": "EUR"}  # extend as needed
            data['Currency'] = currency_map.get(cur_symbol, cur_symbol)
    except Exception as e:
        logging.warning(f"Could not determine currency: {e}")
    # Story text and length
    try:
        # The story content is in a div with id or data-tag (e.g., <div id="content-wrap"> or data-test-id="project-description")
        story_elem = driver.find_element(By.CSS_SELECTOR, "#content-wrap, [data-test-id='project-content']")
        story_text = story_elem.text.strip()
    except NoSuchElementException:
        # If the above fails, try an alternative method: get all text from the main column
        story_text = driver.find_element(By.TAG_NAME, "body").text
    data['Full Story'] = story_text
    # Word count for story
    data['Story Length'] = len(story_text.split())
    # Launch date, End date, State, and Video presence from embedded JSON
    data['Launch Date'] = ""
    data['End Date'] = ""
    data['State'] = ""
    data['Has Video'] = False
    try:
        # Extract the window.current_project JSON from a script tag
        scripts = driver.find_elements(By.TAG_NAME, "script")
        current_project_json = ""
        for script in scripts:
            script_content = script.get_attribute("innerHTML")
            if script_content and "window.current_project" in script_content:
                # Find the JSON string between the quotes
                start = script_content.find('window.current_project = "') + len('window.current_project = "')
                end = script_content.find('";', start)
                if start != -1 and end != -1:
                    json_str = script_content[start:end]
                    # Unescape HTML entities
                    json_str = html.unescape(json_str)
                    current_project_json = json_str
                break
        if current_project_json:
            proj_data = json.loads(current_project_json)
            # launched_at and deadline are likely timestamps (seconds since epoch)
            if 'launched_at' in proj_data:
                dt = datetime.fromtimestamp(proj_data['launched_at'])
                data['Launch Date'] = dt.strftime("%Y-%m-%d")
            if 'deadline' in proj_data:
                dt = datetime.fromtimestamp(proj_data['deadline'])
                data['End Date'] = dt.strftime("%Y-%m-%d")
            if 'state' in proj_data:
                state = proj_data['state']
                # Normalize to our terms
                if state == 'live':
                    data['State'] = 'live'
                elif state == 'successful':
                    data['State'] = 'successful'
                else:
                    # treat 'failed', 'canceled', 'suspended' all as failed if not successful
                    data['State'] = 'failed' if state != 'successful' else 'successful'
            # video object presence
            if proj_data.get('video'):
                data['Has Video'] = True
    except Exception as e:
        logging.warning(f"Could not extract JSON data from page: {e}")
        # Fallback: determine state by pledged vs goal if deadline passed
        if data.get('Goal') and data.get('Pledged'):
            # If no time info, assume if pledged >= goal then successful, else failed (for ended projects)
            data['State'] = 'successful' if data['Pledged'] >= data['Goal'] else 'failed'
        # For launch/end date, leave blank if not obtained
        # Video presence fallback via DOM check:
        try:
            driver.find_element(By.TAG_NAME, "video")
            data['Has Video'] = True
        except NoSuchElementException:
            data['Has Video'] = False
    return data

def main(input_file, output_file):
    # Prepare output CSV file
    file_exists = os.path.isfile(output_file) and os.path.getsize(output_file) > 0
    fout = open(output_file, 'a', newline='', encoding='utf-8')
    writer = csv.writer(fout)
    # Write header if starting fresh
    if not file_exists:
        headers = ["URL", "Project Title", "Category", "Full Story", "Goal", "Pledged",
                   "Currency", "Backers", "Launch Date", "End Date", "State", "Has Video", "Story Length", "Location"]
        writer.writerow(headers)
    # Load already scraped URLs to skip (for resume)
    done_urls = set()
    if file_exists:
        try:
            with open(output_file, 'r', encoding='utf-8') as f:
                for line in f:
                    # URL is the first column in each line (assuming no newline breaks in URL)
                    url = line.split(',', 1)[0].strip()
                    done_urls.add(url)
        except Exception as e:
            logging.error(f"Error reading existing CSV for resume: {e}")
    # Read input URLs
    try:
        with open(input_file, 'r', encoding='utf-8') as fin:
            urls = [u.strip() for u in fin if u.strip()]
    except Exception as e:
        logging.error(f"Failed to read input file {input_file}: {e}")
        return
    driver = init_browser(headless=True)
    for url in urls:
        if url in done_urls:
            logging.info(f"Skipping already scraped URL: {url}")
            continue
        attempt = 0
        success = False
        while attempt < 3 and not success:
            try:
                logging.info(f"Scraping URL ({attempt+1}/3): {url}")
                driver.get(url)
                # Wait for basic page load
                WebDriverWait(driver, 15).until(lambda d: d.execute_script('return document.readyState') == 'complete')
                accept_cookies(driver)
                project_data = get_project_data(driver)
                project_data['URL'] = url
                # Write to CSV
                writer.writerow([project_data.get(col, "") for col in ["URL","Project Title","Category","Full Story","Goal",
                                "Pledged","Currency","Backers","Launch Date","End Date","State","Has Video","Story Length","Location"]])
                fout.flush()
                logging.info(f"Successfully scraped: {url}")
                success = True
            except (TimeoutException, WebDriverException) as e:
                attempt += 1
                logging.warning(f"Error scraping {url} (attempt {attempt}): {e}")
                # If not last attempt, try to recover (e.g., restart driver or refresh)
                if attempt < 3:
                    try:
                        driver.quit()
                    except Exception:
                        pass
                    driver = init_browser(headless=True)
                    continue
                # On final failure, log and capture screenshot
                logging.error(f"Failed to scrape {url} after {attempt} attempts.")
                try:
                    os.makedirs("screenshots", exist_ok=True)
                    screenshot_file = os.path.join("screenshots", f"fail_{int(time.time())}.png")
                    driver.save_screenshot(screenshot_file)
                except Exception as ss_exc:
                    logging.error(f"Failed to save screenshot for {url}: {ss_exc}")
        # End of attempts for this URL
    # End loop
    fout.close()
    driver.quit()
    logging.info("Scraping run complete. Browser closed.")
    if os.path.exists("failed.txt"):
        with open("failed.txt", "r") as f:
            failed_count = len(f.readlines())
        logging.info(f"⚠️ Total failed URLs: {failed_count}")

# Entry point for script
if __name__ == "__main__":
    # Default file paths (could be replaced by command-line args for flexibility)
    input_path = "projects.txt"
    output_path = "kickstarter_data.csv"
    main(input_path, output_path)




