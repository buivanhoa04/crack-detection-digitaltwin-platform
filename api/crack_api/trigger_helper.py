import sys
from pymongo import MongoClient
from celery_tasks import process_video_offline_task

def main():
    # 1. Kết nối tới MongoDB trong Docker
    client = MongoClient('mongodb://mongodb:27017/')
    db = client['digital_twin']
    
    # 2. Reset trạng thái task trong DB
    res = db['tasks'].update_one(
        {'_id': 'task_a46519b3'},
        {'$set': {'processingStatus': 'chờ xử lý', 'ErrorCode': None, 'datas': []}}
    )
    print(f"MongoDB updated! Matches: {res.matched_count}, Modified: {res.modified_count}")
    
    # 3. Kích hoạt task trong Celery
    video_path = '/data/file/sources/2026/06/16/road/task_a46519b3/task_a46519b3_100Ftask_Road_Inspec_1080p.mp4'
    process_video_offline_task.delay('task_a46519b3', video_path, 'road')
    print("Task successfully enqueued in Celery!")

if __name__ == '__main__':
    main()
