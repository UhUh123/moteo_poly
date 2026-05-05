#!/usr/bin/env python3
"""
Скрипт для сбора информации о погодных рынках с Polymarket.
Собирает: название рынка, местоположение (локацию), источник разрешения (сайт).

Как работает:
1. Открывает https://polymarket.com/weather в браузере (headless)
2. Прокручивает страницу, загружая все рынки через infinite scroll
3. Собирает уникальные slug каждого события
4. Для каждого события запрашивает детали через Polymarket Gamma API
5. Парсит описание, извлекая:
   - Местоположение (аэропорт/станция)
   - URL источника разрешения (Wunderground, weather.gov, NASA, USGS и др.)
6. Сохраняет результаты в JSON и CSV
"""

import asyncio
import json
import re
import csv
import ssl
import time
import urllib.request
import urllib.error
from playwright.async_api import async_playwright

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def fetch_event_data(slug, retries=3):
    """Получает данные события через Polymarket Gamma API с повторными попытками."""
    url = f"https://gamma-api.polymarket.com/events?slug={slug}"
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=30, context=ctx) as response:
                data = json.loads(response.read())
                if data and len(data) > 0:
                    return data[0]
        except urllib.error.HTTPError as e:
            if e.code == 403:
                print(f"    Ошибка 403 для {slug} (попытка {attempt+1}/{retries})")
                time.sleep(1)
            else:
                print(f"    HTTP ошибка {e.code} для {slug}: {e}")
                break
        except Exception as e:
            print(f"    Ошибка при получении {slug} (попытка {attempt+1}/{retries}): {e}")
            time.sleep(1)
    return None


async def scroll_and_collect_slugs(page):
    """Прокручивает страницу и собирает все уникальные slug событий."""
    slugs = set()
    last_count = 0
    unchanged = 0
    max_scrolls = 300
    
    for scroll_num in range(max_scrolls):
        links = await page.eval_on_selector_all(
            'a[href^="/event/"]',
            'elements => elements.map(e => e.getAttribute("href"))'
        )
        
        for href in links:
            match = re.search(r'/event/([^/?]+)', href)
            if match:
                slugs.add(match.group(1))
        
        if len(slugs) == last_count:
            unchanged += 1
            if unchanged >= 10:
                break
        else:
            unchanged = 0
            last_count = len(slugs)
        
        await page.evaluate('window.scrollBy(0, 1000)')
        if scroll_num % 15 == 0:
            print(f"  Собрано {len(slugs)} уникальных событий...")
        await asyncio.sleep(0.6)
    
    return list(slugs)


def parse_resolution_info(event_data):
    """Извлекает местоположение и источник разрешения из описания рынка."""
    description = event_data.get("description", "")
    title = event_data.get("title", "")
    resolution_source = event_data.get("resolutionSource", "")
    
    # Ищем все URL в описании
    urls = re.findall(r'https?://[^\s\)\]\>"\']+', description)
    urls = [re.sub(r'[.,;:!?)\]]+$', '', url) for url in urls]
    
    # --- ИЗВЛЕЧЕНИЕ ЛОКАЦИИ ---
    location = ""
    
    # Паттерн 1: "recorded at the [Location] Station/Airport"
    m = re.search(r'recorded at the ([^\n]+?)(?:\s+Station|\s+Airport)', description, re.IGNORECASE)
    if m:
        location = m.group(1).strip()
    
    # Паттерн 2: "for the [Location] Station"
    if not location:
        m = re.search(r'for the ([^\n]+?)(?:\s+Station)', description, re.IGNORECASE)
        if m:
            location = m.group(1).strip()
    
    # Паттерн 3: "at the [Location]" (общий, для глобальных рынков)
    if not location:
        m = re.search(r'at the ([A-Z][^\n,\.]{3,80}?)(?:\s+in\s|\s+on\s|,|\.)', description, re.IGNORECASE)
        if m:
            loc = m.group(1).strip()
            if not loc.startswith('http'):
                location = loc
    
    # Паттерн 4: извлекаем город из заголовка для температурных рынков
    if not location:
        m = re.search(r'(?:highest|lowest) temperature in ([^?]+)', title, re.IGNORECASE)
        if m:
            location = m.group(1).strip()
    
    # Паттерн 5: извлекаем локацию из других типов рынков
    if not location:
        m = re.search(r'(?:precipitation|rain|snow) in ([^?]+)', title, re.IGNORECASE)
        if m:
            location = m.group(1).strip()
    
    # Паттерн 6: для ураганов (US)
    if not location:
        m = re.search(r'(?:hurricane|tornado).*?(?:in\s+the\s+US|in\s+the\s+United States)', title, re.IGNORECASE)
        if m:
            location = "United States"
    
    # Паттерн 7: для землетрясений
    if not location:
        m = re.search(r'earthquake.*?in\s+([^?\n]{2,40})', title, re.IGNORECASE)
        if m:
            location = m.group(1).strip()
    
    # --- ИЗВЛЕЧЕНИЕ ИСТОЧНИКА РАЗРЕШЕНИЯ ---
    source_website = ""
    if resolution_source and resolution_source.startswith("http"):
        source_website = resolution_source
    elif urls:
        # Приоритет: wunderground > weather.gov/noaa > nasa > usgs > cdc > другие
        priority_sources = ['wunderground', 'weather.gov', 'noaa', 'nws', 'nas', 'usgs', 'cdc', 'ncei', 'kma', 'metoffice']
        for keyword in priority_sources:
            for url in urls:
                if keyword in url.lower():
                    source_website = url
                    break
            if source_website:
                break
        if not source_website:
            source_website = urls[0]
    
    return {
        "title": title,
        "slug": event_data.get("slug", ""),
        "location": location,
        "resolution_source_url": source_website,
        "all_urls": urls,
        "description": description,
    }


async def main():
    print("=" * 60)
    print("Сбор погодных рынков с Polymarket")
    print("=" * 60)
    print()
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=USER_AGENT)
        page = await context.new_page()
        
        # Переходим на страницу погоды
        print("[1/4] Переходим на страницу /weather...")
        await page.goto("https://polymarket.com/weather", wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(5)
        
        # Прокручиваем и собираем все slug
        print("[2/4] Прокручиваем страницу и собираем все рынки...")
        slugs = await scroll_and_collect_slugs(page)
        print(f"      Всего найдено {len(slugs)} уникальных рынков\n")
        
        await browser.close()
        
        # Фильтруем только температурные рынки (High Temp + Low Temp)
        print("[3/4] Фильтруем только температурные рынки...")
        temp_slugs = []
        for slug in slugs:
            event_data = fetch_event_data(slug)
            if event_data:
                title = event_data.get("title", "").lower()
                if "temperature" in title:
                    temp_slugs.append(slug)
            await asyncio.sleep(0.15)
        
        print(f"      Температурных рынков: {len(temp_slugs)} (из {len(slugs)} всего)\n")
        
        # Собираем детальную информацию по каждому температурному рынку
        print("[4/4] Получаем детальную информацию по температурным рынкам...")
        results = []
        
        for i, slug in enumerate(temp_slugs, 1):
            print(f"      [{i:3d}/{len(temp_slugs)}] {slug[:50]}", end="")
            event_data = fetch_event_data(slug)
            if event_data:
                info = parse_resolution_info(event_data)
                results.append(info)
                loc = info['location'][:40] if info['location'] else 'N/A'
                src = info['resolution_source_url'][:50] if info['resolution_source_url'] else 'N/A'
                print(f" -> {loc} | {src}")
            else:
                results.append({
                    "title": slug,
                    "slug": slug,
                    "location": "",
                    "resolution_source_url": "",
                    "all_urls": [],
                    "description": "",
                })
                print(" -> НЕТ ДАННЫХ")
            await asyncio.sleep(0.2)
        
        # Сохраняем результаты
        print("\n[4/4] Сохраняем результаты...")
        
        with open("weather_markets.json", "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print("      → weather_markets.json")
        
        with open("weather_markets.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["title", "slug", "location", "resolution_source_url", "description"])
            writer.writeheader()
            for r in results:
                writer.writerow({
                    "title": r["title"],
                    "slug": r["slug"],
                    "location": r["location"],
                    "resolution_source_url": r["resolution_source_url"],
                    "description": r["description"][:500] if r["description"] else "",
                })
        print("      → weather_markets.csv")
        
        # Итоговая статистика
        print()
        print("=" * 60)
        print(f"ГОТОВО! Собрано {len(results)} ТЕМПЕРАТУРНЫХ рынков")
        print("=" * 60)
        
        # Группировка по типу источника
        sources = {}
        for r in results:
            url = r['resolution_source_url']
            if url:
                domain = re.search(r'https?://(?:www\.)?([^/]+)', url)
                if domain:
                    domain_name = domain.group(1)
                    sources[domain_name] = sources.get(domain_name, 0) + 1
        
        print("\nРаспределение по источникам разрешения:")
        for domain, count in sorted(sources.items(), key=lambda x: -x[1]):
            print(f"  {domain:35s} : {count:3d} рынков")
        
        print("\nПримеры собранных данных:")
        for r in results[:10]:
            loc = r['location'] or 'N/A'
            src = r['resolution_source_url'] or 'N/A'
            print(f"  • {r['title'][:45]}...")
            print(f"    Локация:  {loc[:50]}")
            print(f"    Источник: {src[:70]}")
            print()


if __name__ == "__main__":
    asyncio.run(main())
