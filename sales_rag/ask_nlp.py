# Turn a question into a SQL query on the Gold warehouse, run it,
# then answer from those rows.
#   python ask_nlp.py "What was total revenue in 2020?"

#-- read the question from the command line --
import sys
question = " ".join(sys.argv[1:]).strip()
if not question:
    raise SystemExit('Usage: python ask_nlp.py "your question"')

#-- read .env --
from dotenv import load_dotenv
load_dotenv()

#-- ask Gemini --
import os
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

from langchain_google_genai import ChatGoogleGenerativeAI
api_key = os.getenv("GOOGLE_API_KEY")
if not api_key:
    raise SystemExit("Set GOOGLE_API_KEY in .env")
llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", google_api_key=api_key, temperature=0)

sql_question = (
    "Write one T-SQL SELECT that answers this question. "
    "Use only the tables and columns in the context. "
    "Include TOP 50 unless the question asks for one number. "
    "Return only the SQL.\n"
    + question
)
SCHEMA = """
dbo.dimcustomer_gold(CustomerID bigint, CustomerName varchar, Email varchar, First varchar, Last varchar)
dbo.dimdate_gold(OrderDate date, Day int, Month int, Year int, mmmyyyy varchar, yyyymm varchar)
dbo.dimproduct_gold(ItemID bigint, ItemName varchar, ItemInfo varchar)
dbo.factsales_gold(CustomerID bigint, ItemID bigint, OrderDate date, Quantity int, UnitPrice real, Tax real)
dbo.forecast_backtest_gold(Model varchar, Fold bigint, CutoffDate date, HorizonEnd date, Grain varchar, Periods bigint, MAE float, RMSE float, MAPE float, sMAPE float, WAPE float, AccuracyPct float, BiasPct float, R2 float, DirAccuracyPct float, DirPrecision float, DirRecall float, DirF1 float, TP bigint, FP bigint, TN bigint, FN bigint, IsChampion bit, ModelRunTS datetime2, RunMode varchar)
dbo.revenue_forecast_gold(ForecastDate date, HorizonDay bigint, ForecastRevenue float, ForecastLower float, ForecastUpper float, Year int, Month int, yyyymm varchar, mmmyyyy varchar, Model varchar, HistoryEndDate date, ModelRunTS datetime2, RunMode varchar)
dbo.revenue_forecast_history_gold(PeriodStart date, PeriodEnd date, yyyymm varchar, ForecastDays bigint, ForecastRevenue float, ForecastLower float, ForecastUpper float, Model varchar, HistoryEndDate date, ModelRunTS datetime2, MLflowRunId varchar, RunMode varchar)
dbo.revenue_monthly_gold(PeriodStart date, PeriodEnd date, ActualRevenue float, ActualDays bigint, ForecastRevenue float, ForecastLower float, ForecastUpper float, ForecastDays bigint, Year int, Month int, yyyymm varchar, mmmyyyy varchar, TotalRevenue float, RecordType varchar, Model varchar, HistoryEndDate date, ModelRunTS datetime2, MLflowRunId varchar, RunMode varchar)
dbo.revenue_weekly_gold(PeriodStart date, PeriodEnd date, ActualRevenue float, ActualDays bigint, ForecastRevenue float, ForecastLower float, ForecastUpper float, ForecastDays bigint, IsoYear bigint, IsoWeek bigint, YearWeek varchar, TotalRevenue float, RecordType varchar, Model varchar, HistoryEndDate date, ModelRunTS datetime2, RunMode varchar)
Revenue on a sale line is Quantity * UnitPrice + Tax.
Join factsales_gold to dimcustomer_gold on CustomerID, to dimproduct_gold on ItemID, and to dimdate_gold on OrderDate.
"""
PROMPT = """You are an AI assistant that answers questions ONLY using the provided context.

Rules:
1. Answer only from the provided context.
2. If the answer is not in the context, say: "I couldn't find that information in the context."
3. Do not make up facts.
4. Be clear and concise.
5. If the question asks you to write a SQL query, return one T-SQL SELECT and nothing else.
6. If the context is a table of query results, those rows are the answer. State the numbers from the rows.

Context:
{context}

Question:
{question}

Answer:
"""
sql = llm.invoke(PROMPT.format(context=SCHEMA, question=sql_question)).content.strip()
if sql.startswith("```"):
    lines = sql.splitlines() #.splitlines() splits the string into a list of lines, removing the newline characters.
    lines = lines[1:] #drops the first line, which is the opening ```sql of the code block.
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1] #drops the last line if it is the closing ``` of the code block.
    sql = "\n".join(lines).strip()
sql = sql.strip().rstrip(";") #drops any trailing semicolon, which pyodbc does not like.
if not sql.lower().lstrip().startswith("select"):
    raise SystemExit("The model did not return a SELECT:\n" + sql)

#-- sign in and run that query on the Gold SQL endpoint --
import struct
import pyodbc
from azure.identity import AzureCliCredential

token = AzureCliCredential().get_token("https://database.windows.net/.default").token
encoded = token.encode("utf-16-le")
token_struct = struct.pack(f"<I{len(encoded)}s", len(encoded), encoded)
SQL_SERVER = (
    "wsw7jvfvolau5hwgzi4rno2o6u-muomuhpchq7erj73xl37ziuot4"
    ".datawarehouse.fabric.microsoft.com"
)
conn = pyodbc.connect(
    "DRIVER={ODBC Driver 18 for SQL Server};"
    f"SERVER={SQL_SERVER};"
    "DATABASE=Gold;",
    attrs_before={1256: token_struct}, #1256 is the ODBC constant SQL_COPT_SS_ACCESS_TOKEN. It tells the driver "authenticate with this token instead of a password"
)
try:
    cursor = conn.cursor()
    cursor.execute(sql)
    columns = [col[0] for col in cursor.description] #cols
    fetched = cursor.fetchall() #rows
finally:
    conn.close()

if not fetched:
    context = "The query returned no rows.\n" + sql
else:
    lines = [" | ".join(columns)] #e.g "CustomerID | CustomerName | Email | First | Last"
    for row in fetched:
        lines.append(" | ".join("" if value is None else str(value) for value in row)) #e.g "1 | John Doe | john.doe@example.com | John | Doe"
    context = "Query results:\n" + "\n".join(lines)

print(llm.invoke(PROMPT.format(context=context, question=question)).content)
print("\nSQL:")
print(sql)