import mysql.connector
from mysql.connector import Error
from google import genai
import json

# ==============================
# CONFIGURATION
# ==============================

API_KEY = "AIzaSyCsUkJynSPy7llWxdOzDNYwVT931wIpdfk"

DB_CONFIG = {
    "host": "localhost",
    "database": "ailogin",
    "user": "appuser",
    "password": "App@123"
}

MODEL_NAME = "gemini-2.5-flash"

# ==============================
# INITIALIZE GEMINI
# ==============================

client = genai.Client(api_key=API_KEY)

# ==============================
# GENERATE COURSE NAME
# ==============================

def generate_course_name(role, domain, interest, skill_level, goal):

    prompt = f"""
    Generate a professional course title only.

    Role: {role}
    Domain: {domain}
    Interest: {interest}
    Skill Level: {skill_level}
    Goal: {goal}

    Return only plain text title.
    """

    try:
        print("\n🟡 Generating Course Name...")

        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt
        )

        title = response.text.strip()
        print("🟢 Course Name Generated:", title)
        return title

    except Exception as e:
        print("🔴 Course Name API Error:", e)
        return None


# ==============================
# GENERATE FULL COURSE
# ==============================

def generate_full_course(course_name):

    prompt = f"""
    Generate 3 chapters for the course: "{course_name}"

    Rules:
    - Each chapter must have exactly 2 subtopics.
    - Each subtopic content must be 150-200 words.
    - Keep explanations concise.
    - No markdown.
    - No explanation outside JSON.

    Return ONLY valid JSON.

    Format:
    [
      {{
        "chapter_number": 1,
        "chapter_name": "Title",
        "chapter_description": "Short summary",
        "subtopics": [
          {{
            "subtopic_name": "Title",
            "content": "Explanation"
          }},
          {{
            "subtopic_name": "Title",
            "content": "Explanation"
          }}
        ]
      }}
    ]
    """

    try:
        print("\n🟡 Generating Full Course Structure...")

        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config={
                "response_mime_type": "application/json"
            }
        )

        text = response.text

        print("🟢 Raw JSON received")
        print("------ RAW START ------")
        print(text[:800])
        print("------ RAW END ------\n")

        data = json.loads(text)
        print("🟢 JSON Parsed Successfully")

        return data

    except json.JSONDecodeError as e:
        print("🔴 JSON Parsing Error:", e)
        return None

    except Exception as e:
        print("🔴 Full Course API Error:", e)
        return None


# ==============================
# MAIN
# ==============================

def main():

    connection = None
    cursor = None

    try:
        connection = mysql.connector.connect(**DB_CONFIG)

        if connection.is_connected():
            print("🟢 Connected to MySQL database\n")

            cursor = connection.cursor(dictionary=True)

            cursor.execute("""
                SELECT RequirementId, Role, Domain, Interest, SkillLevel, Goal
                FROM requirements
                WHERE CourseGenerated = FALSE
            """)

            records = cursor.fetchall()

            if not records:
                print("⚠ No pending requirements found.")
                return

            for row in records:

                requirement_id = row["RequirementId"]

                print("\n==============================")
                print(f"Processing RequirementId: {requirement_id}")
                print("==============================")

                try:
                    # 1️⃣ Generate Course Name
                    course_name = generate_course_name(
                        row["Role"],
                        row["Domain"],
                        row["Interest"],
                        row["SkillLevel"],
                        row["Goal"]
                    )

                    if not course_name:
                        raise Exception("Course name generation failed")

                    # Insert course
                    cursor.execute(
                        "INSERT INTO course (course_name, requirement_id) VALUES (%s, %s)",
                        (course_name, requirement_id)
                    )

                    course_id = cursor.lastrowid

                    # 2️⃣ Generate Course Content
                    full_course = generate_full_course(course_name)

                    if not full_course:
                        raise Exception("Full course generation failed")

                    print("🟢 Inserting chapters into database...")

                    for index, chapter in enumerate(full_course, start=1):

                        chapter_number = chapter.get("chapter_number", index)
                        chapter_name = chapter.get("chapter_name", "Untitled Chapter")
                        chapter_description = chapter.get("chapter_description", "")

                        cursor.execute("""
                            INSERT INTO chapters
                            (course_id, chapter_number, chapter_name, chapter_description, content)
                            VALUES (%s, %s, %s, %s, %s)
                        """, (
                            course_id,
                            chapter_number,
                            chapter_name,
                            chapter_description,
                            ""
                        ))

                        chapter_id = cursor.lastrowid

                        subtopics = chapter.get("subtopics", [])

                        for sub in subtopics:

                            subtopic_name = sub.get("subtopic_name", "Untitled Subtopic")
                            subtopic_content = sub.get("content", "")

                            cursor.execute("""
                                INSERT INTO subtopics
                                (course_id, chapter_id, subtopic_name)
                                VALUES (%s, %s, %s)
                            """, (
                                course_id,
                                chapter_id,
                                subtopic_name
                            ))

                            subtopic_id = cursor.lastrowid

                            cursor.execute("""
                                INSERT INTO results
                                (course_id, chapter_id, subtopic_id, result)
                                VALUES (%s, %s, %s, %s)
                            """, (
                                course_id,
                                chapter_id,
                                subtopic_id,
                                subtopic_content
                            ))

                    # ✅ NEW: Insert full JSON into generated_courses_json
                    cursor.execute("""
                        INSERT INTO generated_courses_json
                        (requirement_id, course_id, course_name, course_json)
                        VALUES (%s, %s, %s, %s)
                    """, (
                        requirement_id,
                        course_id,
                        course_name,
                        json.dumps(full_course)
                    ))

                    # Update flag
                    cursor.execute("""
                        UPDATE requirements
                        SET CourseGenerated = TRUE
                        WHERE RequirementId = %s
                    """, (requirement_id,))

                    connection.commit()
                    print("🟢 Course generated successfully.")

                except Exception as e:
                    connection.rollback()
                    print("🔴 Error while processing:", e)

    except Error as db_error:
        print("🔴 Database error:", db_error)

    finally:
        if cursor:
            cursor.close()
        if connection and connection.is_connected():
            connection.close()
            print("\n🔵 MySQL connection closed.")


# ==============================
# RUN
# ==============================

if __name__ == "__main__":
    main()