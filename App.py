from fastapi import FastAPI, HTTPException, Query, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional
import google.generativeai as genai
import mysql.connector
from mysql.connector import Error
import json
import logging
from datetime import datetime
import io
from pypdf import PdfReader
from docx import Document

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
API_KEY = "AIzaSyCsUkJynSPy7llWxdOzDNYwVT931wIpdfk"

DB_CONFIG = {
    "host": "localhost",
    "database": "ailogin",
    "user": "appuser",
    "password": "App@123"
}

MODEL_NAME = "gemini-2.5-flash"

# Configure Gemini
genai.configure(api_key=API_KEY)

# Initialize FastAPI
app = FastAPI(
    title="AI Quiz Generator & ATS Scorer API",
    description="Generate quiz questions from course content and analyze resumes for ATS compatibility using Gemini AI",
    version="1.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pydantic models
class QuizOption(BaseModel):
    text: str

class QuizQuestion(BaseModel):
    question: str
    options: List[str]
    correct: int
    points: int = 10

class QuizResponse(BaseModel):
    course_id: int
    course_name: str
    total_questions: int
    questions: List[QuizQuestion]
    generated_at: str

class ATSScoreCategory(BaseModel):
    category: str
    score: int
    max_score: int
    feedback: str

class ATSScoreResponse(BaseModel):
    filename: str
    file_type: str
    overall_score: int
    overall_percentage: float
    grade: str
    categories: List[ATSScoreCategory]
    strengths: List[str]
    weaknesses: List[str]
    recommendations: List[str]
    keyword_analysis: dict
    analyzed_at: str

class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None

# Database functions
def get_db_connection():
    """Create and return a database connection"""
    try:
        connection = mysql.connector.connect(**DB_CONFIG)
        if connection.is_connected():
            logger.info("Successfully connected to database")
            return connection
    except Error as e:
        logger.error(f"Database connection error: {e}")
        raise HTTPException(status_code=500, detail=f"Database connection failed: {str(e)}")

def get_course_data(course_id: int):
    """Fetch course data from database"""
    connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        
        query = """
            SELECT id, requirement_id, course_id, course_name, course_json, created_at
            FROM generated_courses_json
            WHERE course_id = %s
            ORDER BY created_at DESC
            LIMIT 1
        """
        
        cursor.execute(query, (course_id,))
        result = cursor.fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail=f"Course with ID {course_id} not found")
        
        # Parse JSON if it's a string
        if isinstance(result['course_json'], str):
            result['course_json'] = json.loads(result['course_json'])
        
        logger.info(f"Successfully retrieved course: {result['course_name']}")
        return result
        
    except Error as e:
        logger.error(f"Database query error: {e}")
        raise HTTPException(status_code=500, detail=f"Database query failed: {str(e)}")
    finally:
        if connection and connection.is_connected():
            cursor.close()
            connection.close()

def format_course_content(course_json: list) -> str:
    """Format course JSON into readable text for LLM"""
    formatted_content = []
    
    for chapter in course_json:
        chapter_num = chapter.get('chapter_number', 'N/A')
        chapter_name = chapter.get('chapter_name', 'Unknown Chapter')
        chapter_desc = chapter.get('chapter_description', '')
        
        formatted_content.append(f"\n## Chapter {chapter_num}: {chapter_name}")
        if chapter_desc:
            formatted_content.append(f"Description: {chapter_desc}")
        
        subtopics = chapter.get('subtopics', [])
        for subtopic in subtopics:
            subtopic_name = subtopic.get('subtopic_name', 'Unknown Subtopic')
            subtopic_content = subtopic.get('content', '')
            
            formatted_content.append(f"\n### {subtopic_name}")
            formatted_content.append(subtopic_content)
    
    return "\n".join(formatted_content)

def generate_quiz_with_gemini(course_name: str, course_content: str, num_questions: int = 10) -> List[dict]:
    """Generate quiz questions using Gemini AI"""
    try:
        model = genai.GenerativeModel(MODEL_NAME)
        
        prompt = f"""You are an expert quiz generator. Based on the following course content, generate {num_questions} multiple-choice quiz questions.

Course Name: {course_name}

Course Content:
{course_content}

Requirements:
1. Generate EXACTLY {num_questions} questions
2. Each question should have 4 options
3. Questions should cover different chapters and topics
4. Mix difficulty levels (easy, medium, hard)
5. Ensure questions are clear and unambiguous
6. Make sure only ONE option is correct
7. Questions should test understanding, not just memorization

Return the response in VALID JSON format with this exact structure:
{{
  "questions": [
    {{
      "question": "Question text here?",
      "options": ["Option A", "Option B", "Option C", "Option D"],
      "correct": 0,
      "points": 10
    }}
  ]
}}

The "correct" field should be the index (0-3) of the correct option.
Return ONLY the JSON, no additional text or markdown formatting."""

        logger.info("Sending request to Gemini AI...")
        response = model.generate_content(prompt)
        
        # Handle multi-part responses
        response_text = ""
        if hasattr(response, 'text'):
            try:
                response_text = response.text
            except ValueError:
                # If response.text fails, extract from parts
                for part in response.parts:
                    if hasattr(part, 'text'):
                        response_text += part.text
        else:
            # Extract text from parts
            for part in response.parts:
                if hasattr(part, 'text'):
                    response_text += part.text
        
        response_text = response_text.strip()
        
        if not response_text:
            raise ValueError("Empty response from Gemini AI")
        
        # Clean up response - remove markdown code blocks if present
        if response_text.startswith("```json"):
            response_text = response_text[7:]
        if response_text.startswith("```"):
            response_text = response_text[3:]
        if response_text.endswith("```"):
            response_text = response_text[:-3]
        response_text = response_text.strip()
        
        # Parse JSON response
        quiz_data = json.loads(response_text)
        questions = quiz_data.get('questions', [])
        
        if not questions:
            raise ValueError("No questions generated by Gemini")
        
        logger.info(f"Successfully generated {len(questions)} questions")
        return questions
        
    except json.JSONDecodeError as e:
        logger.error(f"JSON parsing error: {e}")
        logger.error(f"Response text: {response_text[:500] if 'response_text' in locals() else 'N/A'}")
        raise HTTPException(status_code=500, detail="Failed to parse Gemini response as JSON")
    except Exception as e:
        logger.error(f"Gemini AI error: {e}")
        raise HTTPException(status_code=500, detail=f"Quiz generation failed: {str(e)}")

# File parsing functions
def extract_text_from_pdf(file_content: bytes) -> str:
    """Extract text from PDF file"""
    try:
        pdf_file = io.BytesIO(file_content)
        reader = PdfReader(pdf_file)
        
        text = ""
        for page in reader.pages:
            text += page.extract_text() + "\n"
        
        return text.strip()
    except Exception as e:
        logger.error(f"PDF extraction error: {e}")
        raise HTTPException(status_code=400, detail=f"Failed to extract text from PDF: {str(e)}")

def extract_text_from_docx(file_content: bytes) -> str:
    """Extract text from DOCX file"""
    try:
        docx_file = io.BytesIO(file_content)
        doc = Document(docx_file)
        
        text = ""
        for paragraph in doc.paragraphs:
            text += paragraph.text + "\n"
        
        # Also extract text from tables
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    text += cell.text + "\n"
        
        return text.strip()
    except Exception as e:
        logger.error(f"DOCX extraction error: {e}")
        raise HTTPException(status_code=400, detail=f"Failed to extract text from DOCX: {str(e)}")

def analyze_ats_score_with_gemini(resume_text: str) -> dict:
    """Analyze resume and generate ATS score using Gemini AI"""
    try:
        model = genai.GenerativeModel(MODEL_NAME)
        
        prompt = f"""You are an expert ATS (Applicant Tracking System) analyzer and career advisor. Analyze the following resume and provide a comprehensive ATS compatibility score.

Resume Content:
{resume_text}

Analyze the resume based on these categories:
1. Formatting & Structure (25 points) - Layout, sections, readability
2. Keywords & Skills (25 points) - Industry keywords, technical skills, relevant terminology
3. Experience & Achievements (20 points) - Quantifiable achievements, impact, relevance
4. Education & Certifications (15 points) - Academic background, professional certifications
5. Contact Information (10 points) - Complete and professional contact details
6. Length & Clarity (5 points) - Appropriate length, clear and concise

Provide detailed feedback for each category, identify strengths and weaknesses, and give actionable recommendations.

Return the response in VALID JSON format with this exact structure:
{{
  "overall_score": 85,
  "categories": [
    {{
      "category": "Formatting & Structure",
      "score": 20,
      "max_score": 25,
      "feedback": "Detailed feedback here"
    }}
  ],
  "strengths": [
    "Strength 1",
    "Strength 2"
  ],
  "weaknesses": [
    "Weakness 1",
    "Weakness 2"
  ],
  "recommendations": [
    "Recommendation 1",
    "Recommendation 2"
  ],
  "keyword_analysis": {{
    "found_keywords": ["keyword1", "keyword2"],
    "missing_keywords": ["keyword3", "keyword4"],
    "keyword_density": "Good"
  }}
}}

Return ONLY the JSON, no additional text or markdown formatting."""

        logger.info("Sending resume to Gemini AI for ATS analysis...")
        response = model.generate_content(prompt)
        
        # Handle multi-part responses
        response_text = ""
        if hasattr(response, 'text'):
            try:
                response_text = response.text
            except ValueError:
                # If response.text fails, extract from parts
                for part in response.parts:
                    if hasattr(part, 'text'):
                        response_text += part.text
        else:
            # Extract text from parts
            for part in response.parts:
                if hasattr(part, 'text'):
                    response_text += part.text
        
        response_text = response_text.strip()
        
        if not response_text:
            raise ValueError("Empty response from Gemini AI")
        
        # Clean up response - remove markdown code blocks if present
        if response_text.startswith("```json"):
            response_text = response_text[7:]
        if response_text.startswith("```"):
            response_text = response_text[3:]
        if response_text.endswith("```"):
            response_text = response_text[:-3]
        response_text = response_text.strip()
        
        # Parse JSON response
        ats_data = json.loads(response_text)
        
        logger.info(f"Successfully analyzed resume with ATS score: {ats_data.get('overall_score', 0)}")
        return ats_data
        
    except json.JSONDecodeError as e:
        logger.error(f"JSON parsing error: {e}")
        logger.error(f"Response text: {response_text[:500] if 'response_text' in locals() else 'N/A'}")
        raise HTTPException(status_code=500, detail="Failed to parse Gemini response as JSON")
    except Exception as e:
        logger.error(f"Gemini AI error: {e}")
        raise HTTPException(status_code=500, detail=f"ATS analysis failed: {str(e)}")

def calculate_grade(percentage: float) -> str:
    """Calculate letter grade based on percentage"""
    if percentage >= 90:
        return "A+ (Excellent)"
    elif percentage >= 80:
        return "A (Very Good)"
    elif percentage >= 70:
        return "B (Good)"
    elif percentage >= 60:
        return "C (Average)"
    elif percentage >= 50:
        return "D (Below Average)"
    else:
        return "F (Poor)"

# API Endpoints
@app.get("/", tags=["Root"])
async def root():
    """Root endpoint with API information"""
    return {
        "message": "AI Quiz Generator & ATS Scorer API",
        "version": "1.0.0",
        "endpoints": {
            "generate_quiz": "/api/quiz/generate/{course_id}",
            "ats_score": "/api/ats/score",
            "health_check": "/health"
        }
    }

@app.get("/health", tags=["Health"])
async def health_check():
    """Health check endpoint"""
    try:
        connection = get_db_connection()
        connection.close()
        return {
            "status": "healthy",
            "database": "connected",
            "gemini_api": "configured",
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "database": "disconnected",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }

@app.get("/api/quiz/generate/{course_id}", response_model=QuizResponse, tags=["Quiz"])
async def generate_quiz(
    course_id: int,
    num_questions: int = Query(default=10, ge=5, le=50, description="Number of questions to generate")
):
    """
    Generate quiz questions for a specific course
    
    Parameters:
    - course_id: The ID of the course from the database
    - num_questions: Number of questions to generate (default: 10, min: 5, max: 50)
    
    Returns:
    - Quiz questions with options and correct answers
    """
    try:
        logger.info(f"Generating quiz for course_id: {course_id}")
        
        # Fetch course data from database
        course_data = get_course_data(course_id)
        course_name = course_data['course_name']
        course_json = course_data['course_json']
        
        # Format course content
        formatted_content = format_course_content(course_json)
        
        # Generate quiz using Gemini
        questions = generate_quiz_with_gemini(course_name, formatted_content, num_questions)
        
        # Format response
        quiz_questions = [
            QuizQuestion(
                question=q['question'],
                options=q['options'],
                correct=q['correct'],
                points=q.get('points', 10)
            )
            for q in questions
        ]
        
        return QuizResponse(
            course_id=course_id,
            course_name=course_name,
            total_questions=len(quiz_questions),
            questions=quiz_questions,
            generated_at=datetime.now().isoformat()
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

@app.get("/api/courses/{course_id}", tags=["Courses"])
async def get_course_info(course_id: int):
    """
    Get course information without generating quiz
    
    Parameters:
    - course_id: The ID of the course from the database
    
    Returns:
    - Course details and structure
    """
    try:
        course_data = get_course_data(course_id)
        
        # Count chapters and topics
        course_json = course_data['course_json']
        num_chapters = len(course_json)
        num_topics = sum(len(chapter.get('subtopics', [])) for chapter in course_json)
        
        return {
            "course_id": course_data['course_id'],
            "course_name": course_data['course_name'],
            "requirement_id": course_data['requirement_id'],
            "num_chapters": num_chapters,
            "num_topics": num_topics,
            "created_at": course_data['created_at'].isoformat() if course_data['created_at'] else None,
            "chapters": [
                {
                    "chapter_number": ch.get('chapter_number'),
                    "chapter_name": ch.get('chapter_name'),
                    "num_subtopics": len(ch.get('subtopics', []))
                }
                for ch in course_json
            ]
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching course info: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/ats/score", response_model=ATSScoreResponse, tags=["ATS"])
async def calculate_ats_score(
    file: UploadFile = File(..., description="Resume file (PDF or DOCX only)")
):
    """
    Calculate ATS (Applicant Tracking System) score for uploaded resume
    
    Parameters:
    - file: Resume file in PDF or DOCX format
    
    Returns:
    - Comprehensive ATS score with category breakdown, strengths, weaknesses, and recommendations
    """
    try:
        # Validate file type
        filename = file.filename.lower()
        if not (filename.endswith('.pdf') or filename.endswith('.docx') or filename.endswith('.doc')):
            raise HTTPException(
                status_code=400, 
                detail="Invalid file type. Only PDF and DOCX files are supported."
            )
        
        logger.info(f"Processing file: {file.filename}")
        
        # Read file content
        file_content = await file.read()
        
        # Extract text based on file type
        if filename.endswith('.pdf'):
            resume_text = extract_text_from_pdf(file_content)
            file_type = "PDF"
        else:
            resume_text = extract_text_from_docx(file_content)
            file_type = "DOCX"
        
        # Check if text was extracted
        if not resume_text or len(resume_text.strip()) < 50:
            raise HTTPException(
                status_code=400,
                detail="Could not extract sufficient text from the file. Please ensure the file contains readable text."
            )
        
        logger.info(f"Extracted {len(resume_text)} characters from {file.filename}")
        
        # Analyze with Gemini
        ats_data = analyze_ats_score_with_gemini(resume_text)
        
        # Calculate percentage and grade
        overall_score = ats_data.get('overall_score', 0)
        overall_percentage = round((overall_score / 100) * 100, 2)
        grade = calculate_grade(overall_percentage)
        
        # Format categories
        categories = [
            ATSScoreCategory(
                category=cat['category'],
                score=cat['score'],
                max_score=cat['max_score'],
                feedback=cat['feedback']
            )
            for cat in ats_data.get('categories', [])
        ]
        
        return ATSScoreResponse(
            filename=file.filename,
            file_type=file_type,
            overall_score=overall_score,
            overall_percentage=overall_percentage,
            grade=grade,
            categories=categories,
            strengths=ats_data.get('strengths', []),
            weaknesses=ats_data.get('weaknesses', []),
            recommendations=ats_data.get('recommendations', []),
            keyword_analysis=ats_data.get('keyword_analysis', {}),
            analyzed_at=datetime.now().isoformat()
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in ATS scoring: {e}")
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)