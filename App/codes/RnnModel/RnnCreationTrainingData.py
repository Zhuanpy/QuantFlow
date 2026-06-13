from .RnnCreationData import TrainingDataCalculate
from App.exts import db
from App.models.strategy.RnnTrainingRecords import RnnTrainingRecords
from datetime import datetime

"""
1. 创建训练数据

数据源已从废弃的 rnn_model（裸 pymysql / LoadRnnModel）迁到 ORM
RnnTrainingRecords（quanttradingsystem.strategy_rnn_training），与训练页同源，
避免出现 "Unknown database 'rnn_model'" 后 load 返回空表再 KeyError 的连环报错。
"""


class RMTrainingData:

    def __init__(self, months: str, start_: str, ):  # _month
        self.month = months
        self.start_date = start_

    def single_stock(self, stock: str):
        calculation = TrainingDataCalculate(stock, self.month, self.start_date)
        calculation.calculation_single()

    def _init_month_batch(self):
        """本月还没有任何记录时，用全部股票记录初始化这一批（parser_month=本月，状态=pending）。

        返回初始化后的记录列表；训练记录表本身为空时返回 None。
        """
        all_records = RnnTrainingRecords.query.all()
        if not all_records:
            return None
        for r in all_records:
            r.parser_month = self.month
            r.model_data = 'pending'
            r.model_check = 'pending'
            r.model_error = 'pending'
        db.session.commit()
        return all_records

    # 一批处理多少只：整批先标 processing，处理完再取下一批
    BATCH_SIZE = 10

    def _mark_batch_processing(self, ids):
        """把一批记录一次性标成 processing，列表里这一批一起显示"处理中"。"""
        if not ids:
            return
        try:
            db.session.rollback()
            (RnnTrainingRecords.query
             .filter(RnnTrainingRecords.id.in_(ids))
             .update({RnnTrainingRecords.model_data: 'processing'},
                     synchronize_session=False))
            db.session.commit()
        except Exception:
            db.session.rollback()

    def _write_status(self, rec_id, ok: bool, err: str = None):
        """独立写回单只状态，避免上一步 calculation 失败污染 session。"""
        try:
            db.session.rollback()  # 清理 calculation 可能留下的脏 session
            r = RnnTrainingRecords.query.get(rec_id)
            if not r:
                return
            if ok:
                r.model_data = 'success'
                r.model_data_timing = datetime.now()
            else:
                r.model_data = 'error'
                r.model_data_timing = None
                if err:
                    r.model_error = err[:500]
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f'状态写回失败 (id={rec_id}): {e}')

    def all_stock(self, progress_cb=None, should_stop=None):
        """对本月批次的股票逐只生成训练数据。

        progress_cb(processed, total, message): 每只开始/结束时回调，供路由把进度写进
            train_state，前端轮询 /api/train_status 即可实时看到"正在处理哪只 + 计数"。
        should_stop(): 返回 True 时在下一只之前提前结束（响应停止按钮）。
        两者都可为 None（命令行直接调用时无需进度上报）。
        """
        # 取本月批次记录；本月还没有则用全部股票记录初始化这一批
        records = RnnTrainingRecords.query.filter_by(parser_month=self.month).all()
        if not records:
            records = self._init_month_batch()
            if not records:
                print('训练记录表为空（strategy_rnn_training），无可处理股票')
                return False

        # 跳过已成功的
        pending = [r for r in records if r.model_data != 'success']
        if not pending:
            print(f'{self.month}月训练数据创建完成')
            return False  # 结束运行，因为没有待处理记录

        total = len(pending)
        if progress_cb:
            progress_cb(0, total, f'准备处理 {total} 只股票')

        failed = 0
        done = 0
        batch_count = (total + self.BATCH_SIZE - 1) // self.BATCH_SIZE
        # 一次取 BATCH_SIZE 只标成 processing，整批处理完再取下一批
        for bi, start in enumerate(range(0, total, self.BATCH_SIZE)):
            if should_stop and should_stop():
                print('收到停止请求，提前结束全部模式循环')
                break

            batch = pending[start:start + self.BATCH_SIZE]
            # 整批先标 processing（列表里这一批一起显示"处理中"）
            self._mark_batch_processing([r.id for r in batch])
            if progress_cb:
                progress_cb(done, total,
                            f'加载第 {bi + 1}/{batch_count} 批（{len(batch)} 只），共 {total} 只')

            for rec in batch:
                if should_stop and should_stop():
                    print('收到停止请求，提前结束全部模式循环')
                    break

                rec_id = rec.id
                stock_ = rec.name or rec.code

                if progress_cb:
                    progress_cb(done, total, f'正在处理 {stock_}（{done + 1}/{total}）')

                print(f'\n计算进度：'
                      f'\n剩余股票: {total - done} 个; 总股票数: {total}个;'
                      f'\n当前股票：{stock_};')

                try:
                    run = TrainingDataCalculate(stock_, self.month, self.start_date)
                    # 走完整 from-1m 管线（重采样→信号→衍生→标准化→.npy）。
                    # 不能用 calculation_read_from_sql：它假设 15m 表已带 DailyVolEmaParser /
                    # Signal / SignalChoice 等衍生列，而本地 15m parquet 只有原始 OHLCV，
                    # 直接进 third_calculate 会 KeyError('DailyVolEmaParser')。
                    run.calculation_single()
                    self._write_status(rec_id, ok=True)
                except Exception as ex:
                    failed += 1
                    print(f'Model Data Create Error: {ex}')
                    self._write_status(rec_id, ok=False, err=str(ex))

                # 处理后更新计数（含失败数）
                done += 1
                if progress_cb:
                    msg = f'已处理 {done}/{total}'
                    if failed:
                        msg += f'，其中失败 {failed} 只'
                    progress_cb(done, total, msg)

        return True


if __name__ == '__main__':
    month_ = '2023-01'
    start_d = '2018-01-01'
    running = RMTrainingData(month_, start_d)
    running.all_stock()
