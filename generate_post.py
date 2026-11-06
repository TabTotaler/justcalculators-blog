import os
import json
import random
from datetime import datetime
from google import genai

def main():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY secret is missing!")

    client = genai.Client(api_key=api_key)

    # 1. Load calculators list
    if not os.path.exists("calculators.json"):
        raise FileNotFoundError("calculators.json not found!")
    
    with open("calculators.json", "r") as f:
        calculators = json.load(f)

    # Pick a random calculator to write about today
    calc = random.choice(calculators)
    print(f"Selected calculator for today: {calc['name']}")

    # 2. Prompt Gemini to generate an SEO-friendly blog post
    prompt = f"""
    You are an expert financial and utility content writer for JustCalculators.app.
    Write a helpful, engaging, SEO-optimized blog post explaining the practical real-world importance of the following calculator tool:

    Tool Name: {calc['name']}
    Tool URL: {calc['url']}
    Description: {calc['description']}

    Requirements:
    - Return ONLY valid JSON with no markdown formatting blocks around it (i.e. do not use ```json ... ```).
    - The JSON must have two keys: "title" and "content".
    - "title": A catchy, SEO-friendly headline for the blog post.
    - "content": HTML formatted body content (use <h2>, <p>, <ul>, <li>, and strong tags where appropriate). 
    - Inside the body content, include a styled call-out box linking to the tool using this exact HTML structure:
      <div class="calculator-callout">
        <p>Try it yourself: <a href="{calc['url']}" target="_blank">{calc['name']}</a> - {calc['description']}</p>
      </div>
    - Provide at least 3-4 insightful paragraphs of valuable context.
    """

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
    )

    # Clean response text in case markdown blocks were returned
    raw_text = response.text.strip()
    if raw_text.startswith("```"):
        raw_text = raw_text.split("```")[1]
        if raw_text.startswith("json"):
            raw_text = raw_text[4:]
        raw_text = raw_text.strip()
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3]

    post_data = json.loads(raw_text.strip())
    
    date_str = datetime.now().strftime("%Y-%m-%d")
    slug = post_data["title"].lower().replace(" ", "-").replace("?", "").replace("!", "")
    slug = "".join(c for c in slug if c.isalnum() or c == "-")[:50]
    filename = f"posts/{date_str}-{slug}.html"

    # Ensure posts directory exists
    os.makedirs("posts", exist_ok=True)

    # 3. Build the individual post HTML file
    post_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{post_data['title']} - JustCalculators Blog</title>
    <link rel="stylesheet" href="../style.css">
</head>
<body>
    <header>
        <h1><a href="../index.html">JustCalculators Blog</a></h1>
        <nav><a href="[https://justcalculators.app](https://justcalculators.app)">Back to JustCalculators</a></nav>
    </header>
    <main>
        <article>
            <h2>{post_data['title']}</h2>
            <div class="post-meta">Published on {date_str}</div>
            {post_data['content']}
        </article>
    </main>
    <footer>
        <p>&copy; {datetime.now().year} JustCalculators.app. All rights reserved.</p>
    </footer>
</body>
</html>
"""

    with open(filename, "w", encoding="utf-8") as f:
        f.write(post_html)
    print(f"Created post file: {filename}")

    # 4. Rebuild index.html (Homepage listing all posts)
    rebuild_index()

def rebuild_index():
    posts = []
    if os.path.exists("posts"):
        for file in sorted(os.listdir("posts"), reverse=True):
            if file.endswith(".html"):
                # Simple extraction of title from file
                filepath = os.path.join("posts", file)
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
                    # Extract title
                    title_start = content.find("<h2>") + 4
                    title_end = content.find("</h2>")
                    title = content[title_start:title_end] if title_start != 3 else "Blog Post"
                    
                    date_part = file[:10]
                    posts.append({
                        "title": title,
                        "url": f"posts/{file}",
                        "date": date_part
                    })

    posts_html = ""
    for p in posts:
        posts_html += f"""
        <article style="margin-bottom: 1.5rem; padding: 1.5rem; background: #fff; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.05);">
            <div class="post-meta">{p['date']}</div>
            <h3 style="margin-bottom: 0.5rem;"><a href="{p['url']}">{p['title']}</a></h3>
        </article>
        """

    index_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>JustCalculators Blog - Daily Automated Insights</title>
    <link rel="stylesheet" href="style.css">
</head>
<body>
    <header>
        <h1><a href="index.html">JustCalculators Blog</a></h1>
        <nav><a href="[https://justcalculators.app](https://justcalculators.app)">Back to JustCalculators</a></nav>
    </header>
    <main>
        <div style="margin-bottom: 2rem;">
            <h2>Latest Posts</h2>
            <p>Automated insights, guides, and practical math tips powered by our calculator tools.</p>
        </div>
        {posts_html if posts_html else "<p>No posts yet. Check back soon!</p>"}
    </main>
    <footer>
        <p>&copy; {datetime.now().year} JustCalculators.app. All rights reserved.</p>
    </footer>
</body>
</html>
"""

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(index_html)
    print("Successfully updated index.html")

if __name__ == "__main__":
    main()
