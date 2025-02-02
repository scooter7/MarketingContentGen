# -*- coding: utf-8 -*-
import asyncio
import os
import logging
import requests
import threading
import time
from datetime import datetime
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv
import streamlit as st

# Import OpenAI clients
from openai import OpenAI  as OpenAICLient  # for blog post generation
from langchain_openai import OpenAI as LangchainOpenAI  # for social post generation

# -----------------------
# Environment and Logging
# -----------------------
load_dotenv()

# Fetch secrets from Streamlit secrets or environment
domain = st.secrets.get("WP_DOMAIN") or os.getenv("WP_DOMAIN")
username = st.secrets.get("WP_USERNAME") or os.getenv("WP_USERNAME")
app_password = st.secrets.get("WP_APP_PASSWORD") or os.getenv("WP_APP_PASSWORD")
openai_api_key = st.secrets.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")

# Ensure required secrets exist
if not all([domain, username, app_password, openai_api_key]):
    raise KeyError("One or more required secrets (WP_DOMAIN, WP_USERNAME, WP_APP_PASSWORD, OPENAI_API_KEY) are missing.")

# Set up WordPress endpoint
endpoint = "/wp-json/wp/v2/posts/"
url = f"{domain}{endpoint}"

# Configure logging
logging.basicConfig(
    filename='app.log',
    filemode='a',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# ---------------------------
# Initialize OpenAI Clients
# ---------------------------
# Client for blog post generation
client = OpenAICLient(api_key=openai_api_key)

# LangChain client for social media post generation (using a lower temperature for deterministic output)
social_llm = LangchainOpenAI(temperature=0)

# ---------------------------
# Global Variables for Cron Job
# ---------------------------
cron_stop_event = threading.Event()  # Event to stop the cron job
cron_thread = None  # Holds the cron thread
cron_topic = None   # Topic to use for cron-generated posts
cron_keywords = None  # Keywords for cron-generated posts

# ---------------------------
# Blog Generation Functions
# ---------------------------
async def generate_blog_content(blog_title, blog_topic, keywords):
    """
    Generate blog content using OpenAI based on title, topic and keywords.
    """
    prompt = (
        f"Create a detailed 15-minute read blog post titled '{blog_title}'. "
        f"Focus on the topic: '{blog_topic}' and incorporate the following keywords: {', '.join(keywords)}. "
        "The blog should be well-structured for developers and businesses, using proper HTML tags like <h1>, <h2>, <p>, "
        "and <code>. Include practical examples, analysis, and applications."
    )
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{'role': 'user', 'content': prompt}]
        )
        content = response.choices[0].message.content
        logging.info("Generated blog content for title: %s", blog_title)
        return content
    except Exception as e:
        logging.error("Failed to generate blog content: %s", str(e))
        return None

async def generate_blog_title(blog_topic, keywords):
    """
    Generate a blog title using OpenAI based on the topic and keywords.
    """
    prompt = (
        f"Generate an engaging and professional blog post title for the topic '{blog_topic}' "
        f"incorporating the keywords: {', '.join(keywords)}. The title should be between 10-20 words, unique, and relevant."
    )
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{'role': 'user', 'content': prompt}]
        )
        title = response.choices[0].message.content.strip().strip('"')
        logging.info("Generated blog title: %s", title)
        return title
    except Exception as e:
        logging.error("Failed to generate blog title: %s", str(e))
        return "Untitled Blog Post"

def publish_blog_post(blog_post_title, blog_content):
    """
    Publish the blog post to WordPress.
    """
    post_data = {
        'title': blog_post_title,
        'content': blog_content,
        'status': 'publish'
    }
    try:
        response = requests.post(
            url,
            auth=HTTPBasicAuth(username, app_password),
            json=post_data
        )
        if response.status_code == 201:
            logging.info("Post created successfully! Post ID: %s", response.json().get('id'))
            return True
        else:
            logging.error("Failed to create post. Status Code: %d, Response: %s", response.status_code, response.text)
            return False
    except Exception as e:
        logging.error("Failed to publish blog post: %s", str(e))
        return False

def cron_function():
    """
    Cron-like function that generates and publishes a blog post every 30 minutes.
    """
    global cron_topic, cron_keywords
    interval = 1800  # 30 minutes in seconds

    while not cron_stop_event.is_set():
        logging.info("Cron job started: Checking if it's time to post.")

        # Generate a blog title dynamically using the cron topic and keywords
        blog_title = asyncio.run(generate_blog_title(cron_topic, cron_keywords))
        logging.info("Generating blog content for: %s", blog_title)
        blog_content = asyncio.run(generate_blog_content(blog_title, cron_topic, cron_keywords))

        if blog_content:
            logging.info("Publishing blog post: %s", blog_title)
            publish_blog_post(blog_title, blog_content)
        else:
            logging.error("Cron job: Failed to generate blog content")

        logging.info("Cron job completed. Next run in 30 minutes.")

        # Sleep in increments to allow checking for the stop signal
        for _ in range(interval // 5):
            if cron_stop_event.is_set():
                logging.info("Cron job stopping...")
                return
            time.sleep(5)

def start_cron_job(topic, keywords):
    """
    Start the cron job with the specified topic and keywords.
    """
    global cron_thread, cron_topic, cron_keywords
    cron_topic = topic
    cron_keywords = keywords
    cron_stop_event.clear()

    if cron_thread is None or not cron_thread.is_alive():
        cron_thread = threading.Thread(target=cron_function, daemon=True)
        cron_thread.start()
        logging.info("Cron job thread started.")

def stop_cron_job():
    """
    Stop the cron job.
    """
    cron_stop_event.set()
    logging.info("Cron job has been stopped.")

# ---------------------------
# Social Media Generation Functions
# ---------------------------
def limit_post_length(content, channel):
    """
    Limit the length of the content based on channel guidelines.
    """
    limits = {
        "X": 280,            # Twitter (now X) character limit
        "Facebook": 2000,    # Facebook recommended limit
        "LinkedIn": 3000,    # LinkedIn recommended limit
        "Instagram": 2200,   # Instagram caption limit
        "TikTok": 150,       # TikTok caption limit
        "Youtube": 1000      # Youtube description (or short post) limit – adjust as needed
    }
    max_length = limits.get(channel, 2000)
    if len(content) <= max_length:
        return content

    truncated = content[:max_length]
    # Find the last sentence delimiter to avoid cutting in the middle of a sentence
    last_delimiter = max(truncated.rfind('.'), truncated.rfind('!'), truncated.rfind('?'))
    if last_delimiter != -1:
        return truncated[:last_delimiter + 1]
    else:
        return truncated

def generate_social_content_with_retry(main_content, selected_channels, retries=3, delay=5):
    """
    Generates social media content for specified channels with retry logic.
    Ensures emojis are preserved properly in UTF-8.
    """
    generated_content = {}

    for channel in selected_channels:
        for attempt in range(retries):
            try:
                # Explicitly request UTF-8 standard emojis in the OpenAI prompt
                prompt = (
                    f"Generate a {channel.capitalize()} post based on this content:\n\n"
                    f"{main_content}\n\n"
                    "The post should be engaging, professional, and include up to 2 standard UTF-8 emojis "
                    "(😊, 🚀, 🎉, 🔥, 🌍, etc.). Avoid using special characters that may not render correctly."
                )

                # Call OpenAI API
                response = social_llm(prompt)

                if response:
                    limited_content = limit_post_length(response.strip(), channel)

                    # ✅ FIXED: Keep text as-is to preserve emojis
                    generated_content[channel] = limited_content 

                break  # Exit retry loop on success

            except Exception as e:
                if attempt < retries - 1:
                    time.sleep(delay)  # Wait before retrying
                else:
                    generated_content[channel] = f"Error generating content: {str(e)}"

    return generated_content

# ---------------------------
# Streamlit UI
# ---------------------------
st.title("Automated WordPress Blog & Social Media Post Creator")

# --- Blog Post Section ---
st.header("Blog Post Creator")

# Display Cron Job Status
if "cron_thread" in st.session_state and st.session_state["cron_thread"] is not None:
    if st.session_state["cron_thread"].is_alive():
        st.warning("🚨 Cron job is currently running!")
    else:
        st.success("✅ Cron job is NOT running.")
else:
    st.success("✅ Cron job is NOT running.")

# Inputs for blog post generation
blog_title = st.text_input("Enter the blog title:",
                           placeholder="e.g., The Future of AI in Software Development")
blog_topic = st.text_input("Enter the blog topic:",
                           placeholder="e.g., Artificial Intelligence in Development")
keywords_str = st.text_area("Enter keywords (comma-separated):",
                            placeholder="e.g., AI, software development, innovation")
keywords = [word.strip() for word in keywords_str.split(",") if word.strip()]

# Option for using the topic/keywords for cron job posting
use_for_cron = st.checkbox("Use this topic and keywords for automated (cron) posting")

# Buttons to start and stop cron job
col1, col2 = st.columns(2)
with col1:
    if st.button("Start Cron Job", key="start_cron_button"):
        if not blog_topic or not keywords:
            st.error("Please enter both a blog topic and keywords to start the cron job.")
        else:
            start_cron_job(blog_topic, keywords)
            st.success("Cron job started!")
with col2:
    if st.button("Stop Cron Job", key="stop_cron_button"):
        stop_cron_job()
        st.success("Cron job stopped.")

# Manual blog post generation
if st.button("Generate and Publish Blog Post", key="manual_generate_button"):
    if not blog_title or not blog_topic or not keywords:
        st.error("Please fill in all fields (title, topic, and keywords) to generate a blog post.")
    else:
        with st.spinner("Generating blog content..."):
            # Generate blog content asynchronously
            blog_content = asyncio.run(generate_blog_content(blog_title, blog_topic, keywords))
            if blog_content:
                published = publish_blog_post(blog_title, blog_content)
                if published:
                    st.success("Blog post published successfully!")
                else:
                    st.error("Failed to publish blog post. Check logs for details.")
            else:
                st.error("Failed to generate blog content.")

# --- Social Media Post Section ---
st.header("Social Media Post Generator")

# For social media, we re-use the blog title, topic, and keywords to create a “main content” prompt.
st.markdown("Based on your blog details, generate social media posts for selected platforms.")

# Select which social channels to generate posts for
selected_channels = st.multiselect(
    "Select Social Media Channels:",
    ["Facebook", "X", "LinkedIn", "Youtube", "Instagram", "TikTok"],
    default=["Facebook", "X"]
)

if st.button("Generate Social Media Posts", key="social_generate_button"):
    if not blog_title or not blog_topic or not keywords:
        st.error("Please provide the blog title, topic, and keywords first.")
    elif not selected_channels:
        st.error("Please select at least one social media channel.")
    else:
        with st.spinner("Generating social media posts..."):
            # Prepare a main content prompt (you can adjust this to include more details if desired)
            main_content = (
                f"Blog Title: {blog_title}\n"
                f"Blog Topic: {blog_topic}\n"
                f"Keywords: {', '.join(keywords)}"
            )
            social_posts = generate_social_content_with_retry(main_content, selected_channels)
            st.session_state["social_content"] = social_posts
            st.success("Social media posts generated!")

# Display generated social media posts (if any)
if "social_content" in st.session_state and st.session_state["social_content"]:
    for channel, content in st.session_state["social_content"].items():
        st.subheader(f"{channel} Post")
        st.text_area(f"Generated Content for {channel}:", content, height=200)
        # Provide a download button for each channel's content
        filename = f"{channel}_post.txt"
        st.download_button(
            label=f"Download {channel} Post",
            data=content,  # Keep text as-is
            file_name=filename,
            mime="text/plain"
        )
