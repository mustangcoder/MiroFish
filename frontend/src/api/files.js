import service from './index'

/** 查询已上传文件。 */
export function listUploadedFiles(params = {}) {
  return service({
    url: '/api/files',
    method: 'get',
    params
  })
}

/** 上传一个或多个文件到文件库。 */
export function uploadFiles(formData) {
  return service({
    url: '/api/files',
    method: 'post',
    data: formData,
    headers: {
      'Content-Type': 'multipart/form-data'
    }
  })
}

/** 修改文件展示名称。 */
export function renameUploadedFile(fileId, data) {
  return service({
    url: `/api/files/${encodeURIComponent(fileId)}`,
    method: 'patch',
    data
  })
}

/** 删除未被项目引用的文件。 */
export function deleteUploadedFile(fileId) {
  return service({
    url: `/api/files/${encodeURIComponent(fileId)}`,
    method: 'delete'
  })
}

/** 查询文件的项目引用。 */
export function getUploadedFileReferences(fileId) {
  return service({
    url: `/api/files/${encodeURIComponent(fileId)}/references`,
    method: 'get'
  })
}

/** 使用当前 API 基址生成文件下载地址。 */
export function uploadedFileDownloadUrl(fileId) {
  const baseUrl = (service.defaults.baseURL || '').replace(/\/$/, '')
  return `${baseUrl}/api/files/${encodeURIComponent(fileId)}/download`
}
