"""导出任务的两个原生接口：查状态（推送断线时轮询用）、下载本人生成好的文件。"""
import frappe

from pearnly_core.exports import service


@frappe.whitelist(methods=["GET"])
def status(job):
    return service.status(job)


@frappe.whitelist(methods=["GET"])
def download(job):
    service.download(job)
